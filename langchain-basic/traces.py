"""Elemento 7 del agente: LA TRAZA.

`result["messages"]` es TODA la conversación, en orden. Cada vuelta del bucle deja dos o tres mensajes:

    👤 Human   lo que preguntó la persona
    🤖 AI      la respuesta del modelo: o PIDE herramientas (tool_calls) o responde con texto (fin del bucle)
    🔧 Tool    el resultado de una herramienta, que vuelve al modelo
"""

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage


def print_trace(messages: list[BaseMessage]) -> None:
    model_calls = 0
    for message in messages:
        if isinstance(message, SystemMessage):
            print(f"⚙️  System  {message.text[:70]}…")
        elif isinstance(message, HumanMessage):
            print(f"👤 Human   {message.text}")
        elif isinstance(message, AIMessage):
            model_calls += 1
            for call in message.tool_calls:
                print(f"🤖 AI      pide {call['name']}({call['args']})")
            if message.text:
                print(f"🤖 AI      {message.text}")
        elif isinstance(message, ToolMessage):
            print(f"🔧 Tool    {message.name} → {message.content}")
    print(f"\n({model_calls} llamadas al modelo en total)")
