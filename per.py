from openai import OpenAI
import os
import json  
from dotenv import load_dotenv

load_dotenv()

client = OpenAI(
    api_key=os.getenv("PERPLEXITY_API_KEY"),
    base_url="https://api.perplexity.ai"  # Required for Perplexity
)

response = client.chat.completions.create(
    model="sonar",  
    messages=[
        {
            "role": "system",
            "content": "You are a helpful assistant"
        },
        {
            "role": "user",
            "content": "manaal noushad aifirelab"
        }
    ],
    # Optional: These params enhance citation quality
    temperature=0.1,  # Lower for more factual, cited responses
    max_tokens=500
)


# Extract the main message content
message_content = response.choices[0].message.content
print("Message Content:")
print(message_content)

# Extract the citations (list of URLs)
citations = response.citations
print("\nCitations:")
for i, citation in enumerate(citations, 1):
    print(f"[{i}] {citation}")

