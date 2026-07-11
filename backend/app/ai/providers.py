from __future__ import annotations

from os import getenv


def get_embedding_model():
    provider = getenv("RAG_EMBEDDING_PROVIDER", "openai").lower()
    if provider == "google":
        from langchain_google_genai import GoogleGenerativeAIEmbeddings

        return GoogleGenerativeAIEmbeddings(
            model=getenv(
                "GOOGLE_EMBEDDING_MODEL",
                "models/gemini-embedding-001",
            ),
            google_api_key=getenv("GOOGLE_API_KEY"),
            output_dimensionality=int(
                getenv("GOOGLE_EMBEDDING_DIMENSIONS", "1536"),
            ),
        )

    from langchain_openai import OpenAIEmbeddings

    return OpenAIEmbeddings(
        model=getenv("OPENAI_EMBEDDING_MODEL", "text-embedding-3-small"),
        api_key=getenv("OPENAI_API_KEY"),
        dimensions=int(getenv("OPENAI_EMBEDDING_DIMENSIONS", "1536")),
    )


def get_chat_model():
    provider = getenv("RAG_CHAT_PROVIDER", "openai").lower()
    temperature = float(getenv("RAG_TEMPERATURE", "0"))
    if provider == "google":
        from langchain_google_genai import ChatGoogleGenerativeAI

        return ChatGoogleGenerativeAI(
            model=getenv("GOOGLE_CHAT_MODEL", "gemini-1.5-flash"),
            google_api_key=getenv("GOOGLE_API_KEY"),
            temperature=temperature,
        )

    from langchain_openai import ChatOpenAI

    return ChatOpenAI(
        model=getenv("OPENAI_CHAT_MODEL", "gpt-4o-mini"),
        api_key=getenv("OPENAI_API_KEY"),
        temperature=temperature,
    )


async def embed_query(user_query: str) -> list[float]:
    embeddings = get_embedding_model()
    return await embeddings.aembed_query(user_query)
