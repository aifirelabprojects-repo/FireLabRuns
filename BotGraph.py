import os
import json
import hashlib
import asyncio
from typing import Annotated, TypedDict, AsyncGenerator
import operator
from collections import OrderedDict
from dotenv import load_dotenv
from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import MemorySaver  
from tenacity import retry, stop_after_attempt, wait_exponential
from KnowledgeBase import cfg

load_dotenv()

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
MODEL_NAME = "gpt-4o-mini"



checkpointer = MemorySaver()

llm = ChatOpenAI(
    model=MODEL_NAME,
    api_key=OPENAI_API_KEY,
    temperature=0.0,
    frequency_penalty=0.0,
    top_p=1.0,
    max_tokens=500,
    streaming=False, 
    model_kwargs={"response_format": {"type": "json_object"}}
)

# Make these global for dynamic reloading
prompt = None
chain = None
graph = None
chat_chain = None

class State(TypedDict):
    messages: Annotated[list, operator.add]

def load_system_prompt():
    from KnowledgeBase import cfg
    MainRules = "<critical_rules>\nCRITICAL RULES (MUST OBEY):\n- Think step-by-step: \n  1) Check {{current_phase}} and {{lead_data}}. \n  2) Scan user's message for username or mobile per <critical_data_capture> and update lead_data if found.\n  3) Validate rules (e.g., NEVER skip phases; ALWAYS check 'q1_email_domain' before Q3). \n  4) Build response. \n  5) Update JSON.\n- For existing clients: Route to CRE after fetch.\n- Output ONLY valid JSON: {{\"answer\": \"Natural response.\", \"options\": [] or [\"Text1\", \"Text2\"], \"phase\": \"next_phase\", \"lead_data\": {{...updated...}}, \"routing\": \"\", \"analysis\": {{\"interest\": \"high/medium/low\", \"mood\": \"excited/neutral\", }}}}\n- NO extra text. Verify JSON before output.\n- NEVER list options (e.g., bullet points, numbered lists) in the 'answer' field. Provide options ONLY in the 'options' array within the JSON output.\n</critical_rules>\n</instructions>\n<phases>\nPhases (FOLLOW SEQUENTIALLY—NEVER SKIP):\n<phase1>Initial Engagement</phase1>: Greet and identify. \"Welcome! I'm "+cfg.get("name", "")+" your dedicated guide at Analytix. We're the government-approved partner for fast-tracking business in Saudi Arabia, trusted by the Ministry. Are you setting up a new business, or an existing client with a question?\"- Provide options in JSON:\"options\": [\"I'm setting up a new business\", \"I'm an existing client\"]\n- If new: Set phase to 'snip_q1'.\n- If existing: \"What's your company name or WhatsApp code?\" → Simulate Odoo fetch (use {{lead_data}}) → Personalize with fetched details (e.g., \"Great to reconnect with [Company Name]—how's the [specific detail] going?\") → Answer or Set phase to 'snip_q0'.\n<phase2>SNIP Qualification (New Only)</phase2>:\n- <q1>Size - Company (set phase 'snip_q1')</q1>: Within this single phase, ask sequentially for missing details using separate questions—do NOT combine into one question. First, ask for company name if 'q1_company' not in lead_data: \"Great! Could you tell me your company name?\" → Store 'q1_company' in lead_data. If only company provided (no email yet), thank and ask next for name if 'username' not captured: \"Thanks for sharing that! To personalize our support, may I have your name?\" → Store/update 'username'. Then ask for phone if 'mobile' not captured: \"Perfect! For quick updates via WhatsApp, what's your mobile number (e.g., +966... )?\" → Store/update 'mobile'. Finally, ask for email if 'q1_email' not in lead_data: \"Awesome, got it! Now, to get started securely, could you share your email?\" → Store 'q1_email' in lead_data. Detect domain: If @gmail.com/@yahoo.com/etc., set 'q1_email_domain': 'personal'; else 'business'. CRITICAL: Stay in 'snip_q1' and ask ONLY the next missing item per response—do NOT advance or ask multiple at once. ONLY advance to 'snip_q2' when ALL are stored ('q1_company', 'username', 'mobile', 'q1_email', and 'q1_email_domain' detected).\n- <q2>Size - Role (set phase 'snip_q2')</q2>: \"What's your role? (e.g., Founder, CEO)\" → Store 'q2_role'. Rapport: \"A Founder! What's exciting about Saudi expansion?\" After: If 'q1_email_domain' == 'personal', advance to 'snip_q2a'; else to 'snip_q3'. NEVER show Q2a for business emails.\n- <q2a>Upsell (set phase 'snip_q2a', ONLY if personal)</q2a>: \"I see a personal email— for secure docs, may I have your business one? Incentive: 20% off advisory!\" → Update 'q1_email'/'q1_email_domain' to business, tag \"High-Intent\", log discount. Advance to 'snip_q3'.\n- <q3>Need - Category (set phase 'snip_q3')</q3>: \"Which core areas are you exploring?\" → Multi-select options from context (e.g., [\"Market Entry\", \"Compliance\", \"Licensing\"]). \"Select one\" Personalize if business email: \"Thanks for the business email—speeds things up! 🚀\" Store 'q3_categories'. Advance to 'snip_q4'.\n- <q4>Interest - Services (set phase 'snip_q4')</q4>: \"Which services interest you most?\" → Dynamic multi-select based on q3_categories (from context). Include: \"Great pick—clients use [services] to [benefit, e.g., launch in 30 days].\" Store 'q4_services'. Advance to 'snip_q5'.\n- <q5>Pain - Activity (set phase 'snip_q5')</q5>: \"Primary activity for licensing? (e.g., IT, trading)\" → Open-ended. Store 'q5_activity'. Enrich with suggestions. Advance to 'snip_q6'.\n- <q6>Implication - Timeline (set phase 'snip_q6')</q6>: \"How soon to start? (1 month, 1-3, 3-6)\" → Options from context. Store 'q6_timeline'. Advance to 'snip_q7'.\n- <q7>Budget (set phase 'snip_q7')</q7>: \"Estimated budget for setup/compliance? Packages: 35k-150k SAR.\" → Open-ended. Store 'q7_budget'. Connect: \"For 50k SAR, Starter Package fits!\" After Q7: Evaluate routing and set phase to 'routing'.\n<phase3>Routing (after snip_q7)</phase3>:\nHigh-Value (1-3 months, budget >50k, business email): \"Assigning [industry] consultant in 1hr.\" → Set \"phase\": \"routing\", \"routing\": \"high_value\".\nNurturing (3-6 months, <50k): \"Sending guides—follow-up soon.\" → Set \"phase\": \"routing\", \"routing\": \"nurturing\".\nUnclear: \"I totally understand—let me connect you to our expert team right away! 😊\" → Set \"phase\": \"routing\", \"routing\": \"cre\".\nLog all to lead_data (simulate Odoo), including any captured 'username' and 'mobile'.\n</phases>\n<few_shot_examples>\nFew-Shot Examples:\nExample 1: Current phase 'snip_q1', user: \"ABC Corp, abc@gmail.com\". → {{\"answer\": \"Thanks, ABC Corp! Noted your email. What's your role? 😊\", \"options\": [], \"phase\": \"snip_q2\", \"lead_data\": {{\"q1_company\": \"ABC Corp\", \"q1_email\": \"abc@gmail.com\", \"q1_email_domain\": \"personal\"}}, \"routing\": \"\", \"analysis\": {{\"interest\": \"medium\", \"mood\": \"neutral\"}}}}\nExample 2: Phase 'snip_q2', user: \"CEO\", lead_data has personal domain. → Advance to q2a, not q3.\nExample 3: user: \"New business, my WhatsApp is +966123456789, name alex\". → {{\"answer\": \"Awesome, excited to help with your new setup in Saudi! Could you tell me your company name and email? 😊\", \"options\": [], \"phase\": \"snip_q1\", \"lead_data\": {{\"username\": \"alex\", \"mobile\": \"+966123456789\"}}, \"routing\": \"\", \"analysis\": {{\"interest\": \"high\", \"mood\": \"excited\"}}}}\nExample 5: Phase 'snip_q1', user: \"My company is XYZ Trading\". Context has scraped details: established 2020, 50 employees, Dubai branch. → {{\"answer\": \"Got it, XYZ Trading! I've fetched some details on your company—looks like you were established around 2020 with about 50 employees and a branch in Dubai, which is a solid foundation for Saudi expansion. Does that match your setup? What's your email so we can get everything secured? 😊\", \"options\": [], \"phase\": \"snip_q2\", \"lead_data\": {{\"q1_company\": \"XYZ Trading\"}}, \"routing\": \"\", \"analysis\": {{\"interest\": \"high\", \"mood\": \"positive\"}}}}\nExample 4: Any phase, user: \"I need to speak with a human expert now.\" → {{\"answer\": \"I totally understand—let me connect you to our expert team right away! 😊\", \"options\": [], \"routing\": \"cre\", \"lead_data\": {{...existing...}}, \"phase\": \"routing\", \"analysis\": {{\"interest\": \"high\", \"mood\": \"frustrated\"}}}}\nHandle objections empathetically. Update {{lead_data}} by merging. Assess interest/mood in analysis.\n</few_shot_examples>"
    SysPrompt="<instructions>" + cfg.get("guidelines", "") + cfg.get("tones", "") + "ALWAYS follow the SNIP qualification flow EXACTLY for new customers. Track state in 'phase' using {{current_phase}}. Personalize with company data from {{lead_data}}. Use open-ended questions for rapport. \n<critical_data_capture>\nCRITICAL: ALWAYS scan the user's message for any provided username (e.g., from WhatsApp handle, name like \"alex\") or mobile number (e.g., \"+966123456789\"). If found in ANY phase (including initial, SNIP, or routing), capture and store them IMMEDIATELY in lead_data as 'username' (for names/handles) and 'mobile' (for phone numbers) respectively. Merge with existing lead_data without overwriting other fields. This applies globally—do NOT limit to specific phases. Standardize keys: use 'username' for names/handles and 'mobile' for phones (e.g., from example: \"name alex\" → 'username': \"alex\"; \"WhatsApp +966123456789\" → 'mobile': \"+966123456789\").\nAdditionally, if the user provides a company name or group code (e.g., \"My company is ABC Corp\" or \"Group code: XYZ123\"), flag it in your internal thinking for external fetch (simulate via {{lead_data}} if already populated). Once details are passed in {{lead_data}} (e.g., company history, industry), IMMEDIATELY incorporate them into your response as described above. Do not ask for details already in {{lead_data}}.</critical_data_capture>" + cfg.get("rules_and_restrictions", "") + MainRules
    return SysPrompt

def initialize_chain():
    """Initialize the prompt, chain, graph, and compiled chat_chain."""
    global prompt, chain, graph, chat_chain
    system_prompt = load_system_prompt()
    prompt = ChatPromptTemplate.from_messages([
        ("system", system_prompt),
        MessagesPlaceholder(variable_name="history"),
        ("human", "Phase: {current_phase}\nLead: {lead_data}\nUser: {input}"),
    ])
    chain = prompt | llm
    graph = StateGraph(State)
    graph.add_node("agent", call_model)
    graph.set_entry_point("agent")
    graph.add_edge("agent", END)
    chat_chain = graph.compile(checkpointer=checkpointer)

MAX_CACHE_SIZE = 10000  
response_cache = OrderedDict()

def _evict_cache_if_needed():
    """Evict oldest entries if cache exceeds limit."""
    while len(response_cache) > MAX_CACHE_SIZE:
        response_cache.popitem(last=False)

@retry(stop=stop_after_attempt(2), wait=wait_exponential(multiplier=1, min=2, max=5))
async def call_model(state: State) -> dict:
    """Async node: LLM call with full history for context retention, cache for repeated queries."""
    messages = state['messages']
    user_input = messages[-1].content

    current_phase = ''
    lead_data = {}
    if len(messages) > 1 and isinstance(messages[-2], AIMessage):
        try:
            prev_json = json.loads(messages[-2].content)
            current_phase = prev_json.get('phase', '')
            lead_data = prev_json.get('lead_data', {})
        except json.JSONDecodeError:
            pass  

    cache_key = hashlib.sha256(f"{user_input}:{current_phase}:{hash(json.dumps(lead_data, sort_keys=True))}".encode()).hexdigest()
    if cache_key in response_cache:
        response_cache.move_to_end(cache_key)  
        return {"messages": [AIMessage(content=response_cache[cache_key])]}

    history = messages[:-1]  
    
    chain_input = {
        "input": user_input,
        "history": history,
        "current_phase": current_phase,
        "lead_data": json.dumps(lead_data)
    }

    result = await chain.ainvoke(chain_input)
    full_content = result.content if isinstance(result, AIMessage) else ""

    try:
        json.loads(full_content)
    except json.JSONDecodeError:
        pass
    
    # Cache the full response
    response_cache[cache_key] = full_content
    response_cache.move_to_end(cache_key)
    _evict_cache_if_needed()
    
    return {"messages": [AIMessage(content=full_content)]}

# Initial setup
initialize_chain()

async def invoke_chat_async(input_text: str, session_id: str) -> AsyncGenerator[str, None]:
    """Invoke the chat chain with per-session memory via thread_id."""
    if chat_chain is None:
        raise ValueError("Chain not initialized. Call initialize_chain() first.")
    config = {"configurable": {"thread_id": session_id}}

    async for chunk in chat_chain.astream(
        {"messages": [HumanMessage(content=input_text)]},
        config,
        stream_mode="values" 
    ):
        if "messages" in chunk and chunk["messages"]:
            new_msg = chunk["messages"][-1]
            if isinstance(new_msg, AIMessage):
                content = new_msg.content or ""
                yield content
                return  

def reload_system_prompt():
    initialize_chain()
    print("System prompt reloaded and chain reinitialized successfully.")  
    

    
