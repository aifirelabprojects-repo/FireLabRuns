import os
import json
import sys
import asyncio
from openai import AsyncOpenAI
from dotenv import load_dotenv

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

load_dotenv()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
client = AsyncOpenAI(api_key=OPENAI_API_KEY)

MODEL_NAME = "gpt-4o-mini"

def simulate_company_enrichment(company: str, question: str) -> str:
    company_lower = company.lower().strip()

    dummy_data = {
        "infratech": {
            "industry": "Infrastructure & Construction",
            "size": "500–1000 employees",
            "headquarters": "Mumbai, India",
            "branches": "12 offices across India"
        },
        "techsoft": {
            "industry": "Software Development",
            "size": "200–500 employees",
            "headquarters": "Bangalore, India",
            "branches": "5 global locations"
        },
        "medilife": {
            "industry": "Healthcare & Biotechnology",
            "size": "1000–5000 employees",
            "headquarters": "Hyderabad, India",
            "branches": "8 offices across India"
        }
    }

    if company_lower in dummy_data:
        data = dummy_data[company_lower]
        enrichment = (
            f"Fetched details about user company: {company} operates in the {data['industry']} industry. "
            f"They have about {data['size']}, headquartered in {data['headquarters']}, "
            f"and around {data['branches']}."
        )
    else:
        enrichment = ""

    return enrichment

functions = [
    {
        "name": "simulate_company_enrichment",
        "description": "Enrich company details if mentioned.",
        "parameters": {
            "type": "object",
            "properties": {
                "company": {"type": "string", "description": "Company name."},
                "question": {"type": "string", "description": "User message."}
            },
            "required": ["company", "question"]
        }
    }
]


def FindTheComp(question: str):
    async def inner_process():
    
        response = await client.chat.completions.create(
            model=MODEL_NAME,
            messages=[
                {"role": "system", "content": "Detect company mentions and enrich if needed."},  # Shorter system prompt
                {"role": "user", "content": question}
            ],
            functions=functions,
            function_call="auto",
            temperature=0.0, 
            max_tokens=500,   
        )

        message = response.choices[0].message
        if message.function_call is not None:
            func_name = message.function_call.name
            args_json = message.function_call.arguments
            args = json.loads(args_json)
            company = args.get("company")
            question_text = args.get("question")
            enrichment_result = simulate_company_enrichment(company, question_text)

         
            if enrichment_result:
            
                followup = await client.chat.completions.create(
                    model=MODEL_NAME,
                    messages=[
                        {"role": "system", "content": "Summarize enrichment data concisely."}, 
                        {"role": "user", "content": question},
                        {"role": "function", "name": func_name, "content": enrichment_result}
                    ],
                    temperature=0.0,
                    max_tokens=300, 
                )

                return followup.choices[0].message.content
            return ""

        return message.content or "No response."

    # Run the async inner function synchronously via asyncio.run()
    return asyncio.run(inner_process())


