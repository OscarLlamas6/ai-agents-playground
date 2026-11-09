"""Las herramientas del agente. La búsqueda es UNA herramienta más: el modelo decide si la usa."""

from langchain.tools import tool

from retriever import Retriever


def build_tools(retriever: Retriever) -> list:
    @tool
    def search_manual(query: str) -> str:
        """Busca en el manual de la mesa de ayuda de TI (contraseñas, VPN, correo, impresoras, equipos, seguridad).

        Devuelve los 3 fragmentos más parecidos, cada uno con su id entre corchetes y un puntaje de 0 a 1.
        Escribe una consulta corta y específica. Si los resultados no responden, reformula y vuelve a llamar.
        """
        hits = retriever.search(query, k=3)
        return "\n".join(f"[{c.id}] ({c.section}, puntaje {s:.2f}) {c.text}" for c, s in hits)

    @tool
    def list_topics() -> str:
        """Lista los temas que cubre el manual. Úsala para saber si una pregunta cae dentro del alcance."""
        return ", ".join(retriever.topics)

    return [search_manual, list_topics]
