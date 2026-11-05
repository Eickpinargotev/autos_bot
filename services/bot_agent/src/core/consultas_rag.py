"""Contrato de consultas estructuradas: límites y duplicados, sin inferir intención."""

MAX_CONSULTAS_RAG = 3
MAX_LARGO_CONSULTA = 2000


def normalizar_consultas(valor) -> list[str]:
    if not isinstance(valor, list):
        return []
    resultado = []
    vistas = set()
    for item in valor:
        if not isinstance(item, str):
            continue
        texto = " ".join(item.split())[:MAX_LARGO_CONSULTA].strip()
        clave = texto.casefold()
        if texto and clave not in vistas:
            resultado.append(texto)
            vistas.add(clave)
        if len(resultado) == MAX_CONSULTAS_RAG:
            break
    return resultado
