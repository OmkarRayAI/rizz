from datetime import datetime
from pydantic import BaseModel
from langchain.agents import AgentExecutor, create_react_agent
from langchain.prompts import PromptTemplate
from langchain.prompts import ChatPromptTemplate, SystemMessagePromptTemplate, HumanMessagePromptTemplate
from langchain.callbacks.base import BaseCallbackHandler
from base import StructuredTool
import asyncio
import time

# Define the template string for the prompt
template_string = """
You are tasked with creating a META PLAN for purpose : {purpose} .Guide the analysis process for answering the user's query based on the user provided instructions. This meta plan will be used by a planning system to develop a comprehensive approach to answering the query.

Your meta plan should provide high-level strategic guidance on the research approach, NOT implementation details or code.

**Meta Planner Tools (for you to use):**
{tools}

**Planner Tools (available to the execution planner):**
{planner_tool_descriptions}

**Important Information:**
- Today's date is {current_date}.

**CRITICALLY IMPORTANT:** 
- Your job is to create a META plan, NOT an execution plan. DO NOT write implementation code, API calls, or specific tool usage syntax.
- A META plan describes WHAT should be done, not HOW it should be implemented in detail.
- You can call any discovery tools provided and any  information on tools or returned by tools to generate the plan.
- DATE CONTEXT IS ESSENTIAL: All analyses must be anchored to a specific time frame. Default to using {current_date} unless the query explicitly mentions a different time period.
- MENTION relevant tools from the Planner Tools list without writing implementation code.
- If query is not relevant to the provided purpose simply return `Query not relevant to purpose`.
- SIMPLICITY IS KEY: If a question can be answered using fewer tools or a straightforward approach, create a simple plan without unnecessary complexity.
- Consider if data visualization might be helpful based on the query, but don't write specific visualization code.

**PLAN COMPLEXITY GUIDANCE:**
- SIMPLE QUESTIONS: For basic factual questions or queries that can be addressed with a single tool, create a brief, focused meta plan mentioning which tool would be appropriate.
- COMPLEX QUESTIONS: For questions requiring multi-faceted analysis, create a comprehensive meta plan that outlines the analytical strategy.
- Use your judgment to determine the appropriate complexity level based on the query.

For SIMPLE QUESTIONS, you may provide a simplified plan with only relevant sections. For COMPLEX QUESTIONS, include these sections:
1. **QUERY UNDERSTANDING**: Brief analysis of what the user is asking about
2. **TEMPORAL CONTEXT**: Specify the date/time period for analysis
3. **DATA CONTEXT**: Relevant information from conversation and interaction with Data and agents 
4. **RESEARCH APPROACH**: High-level strategic guidance on what information to gather and what analyses to perform

**EXAMPLES OF PROPER META PLAN GUIDANCE:**
✓ "Utilize economic indicators data related to steel consumption"
✓ "Analyze quarterly revenue trends for the company"
✓ "Consider comparing with industry peers"

**EXAMPLES OF IMPLEMENTATION DETAILS TO AVOID:**
✗ "economic_indicators_retriever('What are the monthly steel consumption values in MMT?')"
✗ "data_visualization_tool('Create a line chart showing...')"
✗ Specific code snippets or API call formats

Use the following format:

Question: the input question you must answer  
Thought: you should always think about what to do and what data you need to create a meta plan appropriate for the query's complexity
Action: the action to take, should be one of [{tool_names}]  
Action Input: the input to the action  
Observation: the result of the action  
... (this Thought/Action/Action Input/Observation can repeat N times)  
Thought: I now know how to create an appropriate meta plan for this query  
Final Answer: Your meta plan with appropriate sections based on query complexity

Begin!
User Instructions:{user_instructions}
Question: {input}  
Thought:{agent_scratchpad} 
"""

# Create the PromptTemplate for meta plan
meta_plan_prompt = PromptTemplate(
    input_variables=["agent_scratchpad", "input", "tool_names", "tools", "planner_tool_descriptions", "current_date", "purpose", "user_instructions" ],
    template=template_string
)

class StreamingCallbackHandler(BaseCallbackHandler):
    def __init__(self, message_manager, message_type="thinking"):
        self.message_manager = message_manager
        self.message_type = message_type
        self.chunks = []
        self.buffer = ""
        self.last_send_time = time.time()
        
    async def on_llm_new_token(self, token, **kwargs):
        # Accumulate tokens
        self.buffer += token
        self.chunks.append(token)
        
        current_time = time.time()
        # Send a chunk if we have a reasonable amount or hit a sentence boundary or time threshold
        if len(self.buffer) > 20 or any(end in token for end in ['.', '!', '?', '\n']) or (current_time - self.last_send_time) > 0.5:
            try:
                await self.message_manager.send_message({
                    "type": self.message_type, 
                    "content": self.buffer,
                    "is_chunk": True
                })
                self.last_send_time = current_time
                self.buffer = ""
            except Exception as e:
                print(f"Error sending {self.message_type} chunk: {e}")
    
    def get_complete_content(self):
        # Include any remaining buffer content
        result = "".join(self.chunks)
        print(f"Complete {self.message_type} content length: {len(result)}")
        return result

class MetaPlanner:
    def __init__(self):

        self.meta_plan_prompt = meta_plan_prompt
        self.meta_plan = ""
        self.monologue = ""

    async def generate_meta_plan(self, question: str , purpose : str, instructions: str , tool_gen_tools, llm, message_manager=None ) -> str:
        """Generate meta plan first based on the user's query."""
        try:
            # Initialize meta planner tools
            
            """ change this to get dynamic tool lists from defined tools using tool gen"""
            
            # Use tools in the meta planner agent
            tools = tool_gen_tools



            agent = create_react_agent(llm, tools, self.meta_plan_prompt)
            self.agent_executor = AgentExecutor(agent=agent, tools=tools, verbose=True, handle_parsing_errors=True)
            
            # Generate descriptions for planner tools (tools that will be available to the planner)
            planner_tool_descriptions = ""
            for i, tool in enumerate(tools):
                planner_tool_descriptions += f"{i+1}. {tool.name}: {tool.description}\n"
            
            if not planner_tool_descriptions:
                planner_tool_descriptions = "No specific planner tools provided."
            
            # Execute the agent to generate meta plan
            print(f"Generating meta plan for: {question}...")
            result = await self.agent_executor.ainvoke({
                "input": question,
                "current_date": datetime.today().strftime('%Y-%m-%d'),
                "planner_tool_descriptions": planner_tool_descriptions,
                "purpose": purpose,
                "user_instructions":instructions
            })
            
            # Process the result
            output = result['output']
            if output.strip() == "Query not relevant to purpose":
                self.meta_plan = output
                return output
                
            # Store the complete meta plan
            self.meta_plan = f"META PLAN:\n{output}"
            return self.meta_plan
            
        except Exception as e:
            print(f"Error generating meta plan: {e}")
            self.meta_plan = f"META PLAN:\n No Meta Plan generated."
            return self.meta_plan
   
    async def retrieve_meta_data(self, question: str, purpose : str, instructions: str , tools: list[dict], llm, message_manager) -> dict:
        """Process the user query by first creating a meta plan and then converting it to a monologue."""
        try:
            # First, generate the meta plan based on the query
            if message_manager is not None:
                await message_manager.send_message("LLM engine is analyzing query")
            meta_plan = await self.generate_meta_plan(question, purpose , instructions , tools, llm, message_manager)
            
            # Then, convert the meta plan to a human monologue and stream it
            monologue = ""
            
            # Return both the meta plan and monologue
            return {
                "meta_plan": meta_plan,
                "thinking_process": monologue
            }
        except Exception as e:
            print(f"Error in retrieve_meta_data: {e}")
            # Return minimal results in case of error
            return {
                "meta_plan": self.meta_plan if self.meta_plan else f"META PLAN:\nAnalyzing query.",
                "thinking_process": self.monologue if self.monologue else f"I'm thinking about how to analyze."
            }