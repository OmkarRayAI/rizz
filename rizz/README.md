# Rizz
LLM Engine built on top of LLM compiler , build your own tools , own data and go ham. Check example.py for working example

After cloning the repo , you can now use it as a python package
by either importing from the cloned repo directly or installing it as a python package.
If you want to install it as a package then follow the below steps.
use python as 3.11



## Inside the LLMENG-LITE FOlder



## It should look something like this 
LLMENG-LITE/Rizz
LLMENG-LITE/setup.py

## Installation

Install Rizz package with its dependencies(run it from outside where setup.py is kept that is LLMENG-LITE folder)

```bash
  pip install -e .
```
    
## Make a tools folder(here its named as external_tools) for writing your tools that you want the Rizz to use
 Rizz automatcally uses interally defined tools(defined by default) and external tools defined by user (it needs a relative path of external tools folder) Here the tool file uses acrade.dev functions(You can use anything go crazy) and kimik2 llm model to be used as a generic gmail agent.Make sure that the final calling function is a asyncio thread and always wrap the tools using StructuredTool from rizz.base  


```python


import asyncio
from pydantic import BaseModel
from langchain_arcade import ArcadeToolManager
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.memory import MemorySaver
from langgraph.errors import NodeInterrupt
from langgraph.prebuilt import create_react_agent
from rizz.base import StructuredTool

class ExternalGmailTool:
    def __init__(self,extra_info,name,apikey,llm_apikey,userid):
        # Fetch the API key using the provided api_manager
        self.arcade_api_key = apikey
        self.llm_key = llm_apikey
        self.extra = extra_info
        self.name = name
        self.config = {
                    "configurable": {
                        "thread_id": "1",
                        "user_id": userid
                    }
                }
        self.manager = ArcadeToolManager(api_key=self.arcade_api_key)

        # Get all tools from the "Gmail" toolkit
        self.tools = self.manager.get_tools(toolkits=["Gmail"])

    def execute_graph_streaming(self,user_query):
        """
        Execute the graph with streaming and return the final result or error message.

        Args:
            config: Configuration object for the graph execution
            user_query (str): The user's query/message

        Returns:
            dict: Contains 'success' (bool), 'result' (final state), and 'message' (str)
        """

        model = ChatOpenAI(
            openai_api_key=self.llm_key,
            openai_api_base="https://openrouter.ai/api/v1",
            model_name="moonshotai/kimi-k2",  # Free Kimi k2 hosted at openrouter
            temperature=0,
            max_tokens=20000,
            max_retries=2,
        )
        bound_model = model.bind_tools(self.tools)

        memory = MemorySaver()
        user_input = {
            "messages": [
                ("user", user_query)
            ]
            }
        graph = create_react_agent(model=bound_model, tools=self.tools, checkpointer=memory)
        try:
            final_chunk = None
            for chunk in graph.stream(user_input, self.config, stream_mode="values"):
                final_chunk = chunk
                # Optional: print intermediate chunks for debugging
                # print(chunk)

            return {
                'success': True,
                'result': final_chunk,
                'message': 'Execution completed successfully'
            }

        except NodeInterrupt as exc:
            error_msg = f"NodeInterrupt occurred: {exc}. Please authorize the tool or update the request, then re-run."
            return {
                'success': False,
                'result': None,
                'message': error_msg
            }

        except Exception as exc:
            error_msg = f"Unexpected error occurred: {exc}"
            return {
                'success': False,
                'result': None,
                'message': error_msg
            }
    def format_and_get_output(self,Instructions):
        result = self.execute_graph_streaming(Instructions)

        if result['success']:
            print("Success:", result['message'])
            print("Final result:", result['result'])
            #Access specific parts if needed:
            if("messages" in result['result']):
                return str(result['result']["messages"][-1])
        else:
            return "Error:"+ str(result['message'])
    class AgentInput(BaseModel):
        Instructions: str
        MailContent: str


    async def query_agent(self, Instructions: str,MailContent : str) -> str:
        Instructions = Instructions +"Mail Content: "+ MailContent
        result = await asyncio.to_thread(self.format_and_get_output, Instructions= Instructions)
        return result

    def get_tool(self):
        """Method to create and return the StructuredTool"""
        gmailAgent = StructuredTool.from_function(
            func=self.query_agent,
            name=self.name,
            description=(
                "query_agent(Instructions:str,MailContent: str) -> str:\n"
                " - Executes a Gmail Agent with the provided Instructions that includes query and sufficient Mail content required for the set of tasks.\n"
                " - parameters should be  valid  string.\n"
                " - Returns the execution result or error message.\n"
                f" -{self.extra}.\n"
            ),
            args_schema=self.AgentInput,
        )
        return gmailAgent
```


## Here is how main LLM Engine code looks like


```python
import os
import asyncio
from rizz import Rizz
from rizz.prompts import PLANNER_PROMPT, OUTPUT_PROMPT

"""
EXAMPLE USECASE:
AUTOMATE GIUTHUB USING ARCADE AGENT
"""

async def main():
    from langchain_openai import ChatOpenAI
    llm_api_key = "YOUR API KEY"
    llm = ChatOpenAI(
        openai_api_key=llm_api_key,
        openai_api_base="https://openrouter.ai/api/v1",
        model_name="moonshotai/kimi-k2",  # Free Kimi k2 hosted at openrouter
        temperature=0,
        max_tokens=20000,
        max_retries=2,
    )
    
    print("Starting arcade example...")
    
    # Your ACI API key
    arcade_key = "YOUR ARCADE API KEY"
    
    # Define the user query
    question = "List all the latest commits on this repo https://github.com/rootAkash/reinforcement_learning then send a mail to example@mail.com with subject 'test' and body as 'this is an automated git email' mentioning the latest commits "
    
    # Instructions for the LLM
    instructions = """Always follow this workflow:
    Create Meta plan to instruct the planner to use the correct Agents/tools available and how to plan the dataflow between various tools and their
    execution sequence.
    When calling agents you can provide them instruction to do multiple related tasks related to the agents expertise since they can plan by themselves.
    No need to plan for the agents , your plan should be if there are multiple agents them making dataflow of important inputs and outputs possible between them 
    """
    
    # Purpose/system prompt
    purpose = """You are an expert in using Using agents and tools provided to discover and execute actions based on user queries. 
    Always use the provided tools/Agents in sequence to properly handle user requests. Never hallucinate and always stay true to the data u recieve by tool/Agent calls"""
    
    # Define the ACI tools
    tools = [
        {
            "class": "ArcadeGitAgentTool",
            "name": "Github_Agent", 
            "extra_info": "provide clear text instuctions for a task",
            "apikey":arcade_key ,
            "llm_apikey":llm_api_key,
            "userid":"arcade_user@gmail.com",
        },
        {
            "class": "ExternalGmailTool",
            "name": "Gmail_Agent", 
            "extra_info": "provide clear text instuctions for a task",
            "apikey":arcade_key ,
            "llm_apikey":llm_api_key,
            "userid":"arcade_user@gmail.com",
        }
    ]
    
    # Initialize the LLM engine
    engine = Rizz(llm=llm)
    
    # Run the engine with the question and tools
    results = await engine.run(
        question=question,
        purpose=purpose,
        tools=tools,
        instructions=instructions,
        planner_example_prompt=PLANNER_PROMPT,
        joinner_prompt=OUTPUT_PROMPT,
        tool_path="./external_tools" #path to external tools
    )
    
    print("Results:", results)


if __name__ == "__main__":
    # Set the event loop policy for Windows compatibility
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())
```

