from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.runnables.history import RunnableWithMessageHistory
from langchain_community.chat_message_histories import SQLChatMessageHistory
from sqlalchemy import create_engine

llm = ChatOpenAI(
    model="qwen/qwen-2.5-coder-32b-instruct:free",
    api_key="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",
    base_url="https://openrouter.ai/api/v1",
    temperature=0.3,  
    max_tokens=800,
)

engine = create_engine("sqlite:///chat_memory.db")

def get_memory(session_id: str):
    return SQLChatMessageHistory(
        session_id=session_id,
        connection=engine,
    )

prompt = ChatPromptTemplate.from_messages([
    ("system", "You are a helpful and factual assistant. "
               "If you don’t know the answer, say 'I’m not sure.'"),
    MessagesPlaceholder(variable_name="history"),
    ("human", "{input}"),
])

chain = prompt | llm

chat_chain = RunnableWithMessageHistory(
    chain,
    get_memory,                     
    input_messages_key="input",     
    history_messages_key="history",  
)

if __name__ == "__main__":
    print("🤖 Persistent Chat started. Type 'exit' to quit.\n")
    session_id = "user1"  # you can create multiple sessions per user if needed

    while True:
        user_input = input("You: ")
        if user_input.lower() in ["exit", "quit"]:
            print("👋 Goodbye!")
            break

        response = chat_chain.invoke(
            {"input": user_input},
            config={"configurable": {"session_id": session_id}},
        )
        print("Assistant:", response.content)
