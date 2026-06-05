from datetime import datetime
from pydantic import BaseModel
from langchain.agents import AgentExecutor, create_react_agent
from langchain.prompts import PromptTemplate
from langchain.prompts import ChatPromptTemplate, SystemMessagePromptTemplate, HumanMessagePromptTemplate
from langchain.callbacks.base import BaseCallbackHandler
from .base import StructuredTool
import asyncio
import time
from .defaults import default_query_understanding,default_temporal_context,default_research_approach,default_dos,default_donts,default_meta_example
# # Fixed MetaPlanner template
template_string = """
You are tasked with creating a META PLAN for purpose: {purpose}. Guide the analysis process for answering the user's query based on the user provided instructions. This meta plan will be used by a planning system to develop a comprehensive approach to answering the query.

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
- DO NOT CALL ANY TOOLS or AGENTS AT ALL. Just use your information on tools to generate the plan.
- DATE CONTEXT IS ESSENTIAL: All analyses must be anchored to a specific time frame. Default to using {current_date} unless the query explicitly mentions a different time period.
- MENTION relevant tools from the Planner Tools list without writing implementation code.
- If query is not relevant to the provided purpose simply return `Query not relevant to purpose`.
- SIMPLICITY IS KEY: If a question can be answered using fewer tools or a straightforward approach, create a simple plan without unnecessary complexity.
- Consider if data visualization might be helpful based on the query, but don't write specific visualization code.

**PLAN COMPLEXITY GUIDANCE:**
- SIMPLE QUESTIONS: For basic factual questions or queries that can be addressed with a single tool, create a brief, focused meta plan mentioning which tool would be appropriate.
- COMPLEX QUESTIONS: For questions requiring multi-faceted analysis, create a comprehensive meta plan that outlines the analytical strategy.
- Use your judgment to determine the appropriate complexity level based on the query.

**WORKSPACE TOPOLOGY (advanced — only when truly parallel coding tracks exist):**

If the plan involves *multiple independent coding-agent tracks* — i.e. groups
of tasks that can run in their own git branches without sharing files —
emit a fenced block at the very end of your meta plan named WORKSPACE_TOPOLOGY:

```
WORKSPACE_TOPOLOGY:
  wsA: 1, 2
  wsB: 3, 4
  default: wsA
```

Rules:
- Group names are short identifiers ([A-Za-z_][A-Za-z0-9_]{{0,31}}) like wsA, wsB, ws_docs.
- Each task index appears in at most one group.
- The optional `default:` line names the group that hosts the final `join()`.
- OMIT THE BLOCK ENTIRELY for simple/single-track plans. Most plans should not
  emit a topology block. The engine falls back to single-workspace mode and
  behaves as if topology was never declared.

When to emit:
- The plan has two or more sets of tasks that touch disjoint files (impl vs
  docs, frontend vs backend, two independent features).
- Each group could in principle ship as its own pull request.

When NOT to emit:
- The plan is a single sequence of dependent steps.
- The plan involves any non-coding tools (search, retrieval, viz). Those
  tools don't mutate workspaces; topology adds no value.
- You're unsure. Single-workspace mode is correct unless the parallelism is
  unambiguous.

For SIMPLE QUESTIONS, you may provide a simplified plan with only relevant sections. For COMPLEX QUESTIONS, include these sections:
1. **QUERY UNDERSTANDING**: {query_understanding}
2. **TEMPORAL CONTEXT**: {temporal_context}
3. **DATA CONTEXT**: Relevant information from conversation and interaction with Data and agents 
4. **RESEARCH APPROACH**:{research_approach}

**EXAMPLES OF PROPER META PLAN GUIDANCE:**
{dos}

**EXAMPLES OF IMPLEMENTATION DETAILS TO AVOID:**
{donts}

**IMPORTANT: {meta_example}

Begin! Answer the question with a meta plan only.

User Instructions: {user_instructions}
Question: {input}

Meta Plan:"""

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

# Fixed MetaPlanner
class MetaPlanner:
    def __init__(self):
        # Use a simple prompt template without ReAct format for meta planning
        self.meta_plan_prompt = PromptTemplate(
            input_variables=["input", "planner_tool_descriptions", "current_date", 
                             "purpose", "user_instructions", "tools","query_understanding","temporal_context",
                             "research_approach","dos","donts","meta_example"],
            template=template_string
        )
        self.meta_plan = ""
        self.monologue = ""

    async def generate_meta_plan(self, question: str, purpose: str, instructions: str, tool_gen_tools, llm, message_manager=None,query_understanding="",temporal_context="",research_approach="",dos="",donts="",meta_example="") -> str:
        """Generate meta plan based on the user's query without using ReAct agent."""
        try:
            # Generate descriptions for planner tools
            planner_tool_descriptions = ""
            tools_description = ""
            
            for i, tool in enumerate(tool_gen_tools):
                tool_desc = f"{i+1}. {tool.name}: {tool.description}\n"
                planner_tool_descriptions += tool_desc
                tools_description += tool_desc
            
            if not planner_tool_descriptions:
                planner_tool_descriptions = "No specific planner tools provided."
                tools_description = "No tools available."
            # Create the prompt directly without ReAct agent
            prompt_input = {
                "input": question,
                "current_date": datetime.today().strftime('%Y-%m-%d'),
                "planner_tool_descriptions": planner_tool_descriptions,
                "purpose": purpose,
                "user_instructions": instructions,
                "tools": tools_description,
                "query_understanding":default_query_understanding if query_understanding == "" else query_understanding,
                "temporal_context":default_temporal_context if temporal_context == "" else temporal_context,
                "research_approach":default_research_approach if research_approach == "" else research_approach,
                "dos":default_dos if dos == "" else dos,
                "donts":default_donts if donts == "" else donts,
                "meta_example":default_meta_example if meta_example == "" else meta_example,
            }
            
            # Format the prompt
            formatted_prompt = self.meta_plan_prompt.format(**prompt_input)
            
            print(f"Generating meta plan for: {question}...")
            
            # Call LLM directly instead of using ReAct agent
            result = await llm.ainvoke(formatted_prompt)
            
            # Extract content from result
            if hasattr(result, 'content'):
                output = result.content
            else:
                output = str(result)
            
            # Process the result
            if output.strip() == "Query not relevant to purpose":
                self.meta_plan = output
                return output
                
            # Store the complete meta plan
            self.meta_plan = f"META PLAN:\n{output}"
            return self.meta_plan
            
        except Exception as e:
            print(f"Error generating meta plan: {e}")
            self.meta_plan = f"META PLAN:\nError generating plan: {str(e)}"
            return self.meta_plan
   
    async def retrieve_meta_data(self, question: str, purpose: str, instructions: str, tools: list, llm, message_manager,query_understanding="",temporal_context="",research_approach="",dos="",donts="",meta_example="") -> dict:
        """Process the user query by first creating a meta plan."""
        try:
            # First, generate the meta plan based on the query
            if message_manager is not None:
                await message_manager.send_message("LLM engine is analyzing query")
            
            meta_plan = await self.generate_meta_plan(question, purpose, instructions, tools, llm, message_manager,query_understanding,temporal_context,research_approach,dos,donts,meta_example)
            print("meta plan: ",meta_plan)
            
            # Return both the meta plan and thinking process
            return {
                "meta_plan": meta_plan,
                "thinking_process": "Meta plan generated successfully"
            }
        except Exception as e:
            print(f"Error in retrieve_meta_data: {e}")
            # Return minimal results in case of error
            return {
                "meta_plan": self.meta_plan if self.meta_plan else f"META PLAN:\nAnalyzing query: {question}",
                "thinking_process": f"Error occurred: {str(e)}"
            }