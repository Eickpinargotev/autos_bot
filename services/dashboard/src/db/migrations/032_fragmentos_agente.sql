-- Catálogo versionado de fragmentos literales que usan los agentes IA.
-- La identidad (CATEGORIA.CODIGO) es estable; cada guardado crea una versión.

CREATE TABLE IF NOT EXISTS fragmento_categorias (
    id              BIGSERIAL PRIMARY KEY,
    proyecto_id     INTEGER NOT NULL REFERENCES clientes_whatsapp(id) ON DELETE CASCADE,
    codigo          VARCHAR(60) NOT NULL,
    nombre          VARCHAR(120) NOT NULL DEFAULT '',
    activa          BOOLEAN NOT NULL DEFAULT TRUE,
    creado_por      VARCHAR(120) NOT NULL DEFAULT '',
    creado_en       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    actualizado_en  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (proyecto_id, codigo),
    UNIQUE (id, proyecto_id)
);

CREATE TABLE IF NOT EXISTS agente_fragmentos (
    id                  BIGSERIAL PRIMARY KEY,
    proyecto_id         INTEGER NOT NULL REFERENCES clientes_whatsapp(id) ON DELETE CASCADE,
    categoria_id        BIGINT NOT NULL,
    codigo              VARCHAR(60) NOT NULL,
    activo              BOOLEAN NOT NULL DEFAULT TRUE,
    variante_de_id      BIGINT,
    condicion_variante  VARCHAR(40) NOT NULL DEFAULT '',
    version_activa_id   BIGINT,
    creado_por          VARCHAR(120) NOT NULL DEFAULT '',
    creado_en           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    actualizado_en      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (categoria_id, codigo),
    UNIQUE (id, proyecto_id),
    CONSTRAINT fk_fragmento_categoria_proyecto FOREIGN KEY (categoria_id, proyecto_id)
        REFERENCES fragmento_categorias(id, proyecto_id) ON DELETE CASCADE,
    CONSTRAINT ck_fragmento_condicion CHECK (
        condicion_variante IN ('', 'cliente_registrado')
    )
);

CREATE TABLE IF NOT EXISTS agente_fragmento_versiones (
    id              BIGSERIAL PRIMARY KEY,
    proyecto_id     INTEGER NOT NULL REFERENCES clientes_whatsapp(id) ON DELETE CASCADE,
    fragmento_id    BIGINT NOT NULL,
    version         INTEGER NOT NULL,
    mensajes        JSONB NOT NULL DEFAULT '[]'::jsonb,
    reporte         TEXT NOT NULL DEFAULT '',
    retomar         TEXT NOT NULL DEFAULT '',
    creado_por      VARCHAR(120) NOT NULL DEFAULT '',
    creado_en       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (fragmento_id, version),
    UNIQUE (id, fragmento_id),
    CONSTRAINT fk_version_fragmento_proyecto FOREIGN KEY (fragmento_id, proyecto_id)
        REFERENCES agente_fragmentos(id, proyecto_id) ON DELETE CASCADE,
    CONSTRAINT ck_version_mensajes_array CHECK (jsonb_typeof(mensajes) = 'array')
);

ALTER TABLE agente_fragmentos
    ADD CONSTRAINT fk_fragmento_version_activa
    FOREIGN KEY (version_activa_id, id)
    REFERENCES agente_fragmento_versiones(id, fragmento_id);

ALTER TABLE agente_fragmentos
    ADD CONSTRAINT fk_fragmento_variante_proyecto
    FOREIGN KEY (variante_de_id, proyecto_id)
    REFERENCES agente_fragmentos(id, proyecto_id);

CREATE TABLE IF NOT EXISTS agente_fragmento_asignaciones (
    proyecto_id     INTEGER NOT NULL REFERENCES clientes_whatsapp(id) ON DELETE CASCADE,
    fragmento_id    BIGINT NOT NULL,
    agente          VARCHAR(30) NOT NULL,
    PRIMARY KEY (fragmento_id, agente),
    CONSTRAINT fk_asignacion_fragmento_proyecto FOREIGN KEY (fragmento_id, proyecto_id)
        REFERENCES agente_fragmentos(id, proyecto_id) ON DELETE CASCADE,
    CONSTRAINT ck_asignacion_agente CHECK (agente IN (
        'SUPERVISOR', 'GENERAL', 'CURSO_TEORICO', 'ALQUILER',
        'CLASES', 'DICTAMEN', 'TRAMITES'
    ))
);

CREATE INDEX IF NOT EXISTS idx_fragmentos_proyecto_categoria
    ON agente_fragmentos (proyecto_id, categoria_id, activo);
CREATE INDEX IF NOT EXISTS idx_fragmentos_asignacion
    ON agente_fragmento_asignaciones (proyecto_id, agente);
CREATE INDEX IF NOT EXISTS idx_fragmentos_versiones
    ON agente_fragmento_versiones (fragmento_id, version DESC);
