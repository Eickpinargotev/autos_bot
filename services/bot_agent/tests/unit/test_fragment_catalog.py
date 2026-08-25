import os
import unittest
from unittest.mock import patch


os.environ.setdefault("TELEGRAM_BOT_TOKEN", "test-token")
os.environ.setdefault("POSTGRES_URL", "postgresql://user:pass@localhost:5432/test")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/15")

from src.application.fragment_catalog import (
    AREA_FRAGMENTS,
    SPECIALIST_AREAS,
    allowed_fragments,
    catalog_for_prompt,
    get_fragment,
    resolve_variant,
    visible_fragment_ids,
)


class FragmentCatalogTests(unittest.TestCase):
    def test_fragments_come_from_mensajes_json(self):
        frag = get_fragment("CLASES.C2")
        self.assertIsNotNone(frag)
        self.assertTrue(frag.messages)
        self.assertTrue(frag.report)

        first = get_fragment("GENERAL.G1")
        self.assertIn("Enrique Guzmán", first.messages[0])

    def test_orchestrator_categories_are_not_fragments(self):
        for excluded in ("KEYWORD.T1", "PUBLICIDAD.P1", "WELCOME.W"):
            self.assertIsNone(get_fragment(excluded))

    def test_keyword_variants_are_hidden_from_the_agent(self):
        visible = visible_fragment_ids()
        self.assertIn("DICTAMEN.D1", visible)
        for hidden in ("DICTAMEN.D1_1", "GENERAL.G16_1", "GENERAL.G28_1"):
            self.assertNotIn(hidden, visible)

    def test_resolve_variant_swaps_when_registered(self):
        with patch(
            "src.infrastructure.repositories.keyword_registry_repository.KeywordRegistryRepository.exists",
            return_value=True,
        ):
            self.assertEqual(resolve_variant("DICTAMEN.D1", "506", "whatsapp"), "DICTAMEN.D1_1")
        with patch(
            "src.infrastructure.repositories.keyword_registry_repository.KeywordRegistryRepository.exists",
            return_value=False,
        ):
            self.assertEqual(resolve_variant("DICTAMEN.D1", "506", "whatsapp"), "DICTAMEN.D1")

    def test_historical_record_without_known_keyword_uses_all_alternate_sinpes(self):
        """El CSV legado no dice si fue tareas o transporte; la fila basta."""
        historical = {
            "id": 7,
            "registro": "50688887777",
            "canal": "whatsapp",
            "palabra_clave": "",
        }
        with patch(
            "src.infrastructure.repositories.keyword_registry_repository.consultar_uno",
            return_value=historical,
        ):
            for base, alternate in (
                ("DICTAMEN.D1", "DICTAMEN.D1_1"),
                ("GENERAL.G16", "GENERAL.G16_1"),
                ("GENERAL.G28", "GENERAL.G28_1"),
            ):
                resolved = resolve_variant(base, historical["registro"], historical["canal"])
                self.assertEqual(resolved, alternate)
                self.assertIn("61103205", "\n".join(get_fragment(resolved).messages))

    def test_resolve_variant_fails_safe_on_registry_error(self):
        with patch(
            "src.infrastructure.repositories.keyword_registry_repository.KeywordRegistryRepository.exists",
            side_effect=RuntimeError("nocodb down"),
        ):
            self.assertEqual(resolve_variant("GENERAL.G16", "506", "whatsapp"), "GENERAL.G16")

    def test_non_variant_fragment_never_touches_registry(self):
        with patch(
            "src.infrastructure.repositories.keyword_registry_repository.KeywordRegistryRepository.exists",
        ) as exists_mock:
            self.assertEqual(resolve_variant("CLASES.C2", "506", "whatsapp"), "CLASES.C2")
        exists_mock.assert_not_called()

    def test_area_partition_matches_design_v3(self):
        # Diseño v3 (docs/diseno_especialistas.md): 6 especialistas; G4 es de
        # CURSO_TEORICO (no de GENERAL) y TRAMITES no tiene textos curados.
        self.assertEqual(
            SPECIALIST_AREAS,
            ("GENERAL", "CURSO_TEORICO", "ALQUILER", "CLASES", "DICTAMEN", "TRAMITES"),
        )
        self.assertEqual(set(AREA_FRAGMENTS), {"SUPERVISOR", *SPECIALIST_AREAS})
        self.assertEqual(allowed_fragments("CURSO_TEORICO"), {"GENERAL.G4"})
        self.assertNotIn("GENERAL.G4", allowed_fragments("GENERAL"))
        self.assertEqual(allowed_fragments("TRAMITES"), set())
        # Cada fragmento listado existe de verdad en mensajes.json.
        for area, fragment_ids in AREA_FRAGMENTS.items():
            for fid in fragment_ids:
                self.assertIsNotNone(get_fragment(fid), f"{area}: {fid} no existe")

    def test_catalog_for_prompt_contains_literal_texts_and_tags(self):
        catalog = catalog_for_prompt()
        self.assertIn("[[frag:GENERAL.G1]]", catalog)
        self.assertIn("Enrique Guzmán", catalog)
        self.assertNotIn("[[frag:DICTAMEN.D1_1]]", catalog)
        self.assertNotIn("[[frag:KEYWORD.T1]]", catalog)


if __name__ == "__main__":
    unittest.main()
