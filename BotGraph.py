import os
import sqlite3
from typing import Annotated, TypedDict
import operator
from dotenv import load_dotenv
from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.graph import StateGraph, END
from langgraph.checkpoint.sqlite import SqliteSaver

from prompt import AnalytxPromptTemp

load_dotenv()


OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
MODEL_NAME = "gpt-4o-mini"

llm = ChatOpenAI(
    model=MODEL_NAME,
    api_key=OPENAI_API_KEY,
    temperature=0.0,
    frequency_penalty=0.0,  
    top_p=1.0,  
    max_tokens=1000,
    model_kwargs={"response_format": {"type": "json_object"}} 
)

prompt = ChatPromptTemplate.from_messages([
    ("system", AnalytxPromptTemp),
    MessagesPlaceholder(variable_name="history"),
    ("human", "{input}"),
])

chain = prompt | llm

# LangGraph State Definition
class State(TypedDict):
    messages: Annotated[list, operator.add]

# Node function to mimic the original chain behavior
def call_model(state: State) -> dict:
    messages = state['messages']
    # History is all messages except the last (current user input)
    history = messages[:-1]
    # Current input
    user_input = messages[-1].content
    
    # Prepare input for the original chain
    chain_input = {
        "input": user_input,
        "history": history
    }
    
    # Invoke the original chain
    result = chain.invoke(chain_input)
    
    # Return updated state with AI response (preserves JSON output in content)
    return {"messages": [AIMessage(content=result.content)]}

# Build the graph (single node for simplicity, mimicking the linear chain)
graph = StateGraph(State)
graph.add_node("agent", call_model)
graph.set_entry_point("agent")
graph.add_edge("agent", END)

# Fixed checkpointer usage: Create a persistent SQLite connection to avoid context manager issues
conn = sqlite3.connect("chat.db", check_same_thread=False)
checkpointer = SqliteSaver(conn)

# Compile the graph (this is your new `chat_chain`)
chat_chain = graph.compile(checkpointer=checkpointer)


def invoke_chat(input_text: str, session_id: str):
    result = chat_chain.invoke(
        {"messages": [HumanMessage(content=input_text)]},
        config={"configurable": {"thread_id": session_id}}
    )
    # Extract content to match original `getattr(result, "content", str(result))` behavior
    raw_output = result["messages"][-1].content
    # Wrap in AIMessage for exact compatibility if needed elsewhere
    return AIMessage(content=raw_output)
