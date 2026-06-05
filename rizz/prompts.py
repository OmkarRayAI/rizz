from .constants import END_OF_PLAN, JOINNER_FINISH, JOINNER_REPLAN

PLANNER_PROMPT = (
    "Question: Provide a detailed analysis of the current macroeconomic conditions affecting Tesla and Apple, highlighting their stock performance, financial fundamentals, and key factors influencing their technological and sustainability efforts.\n"

    "Thought: To address this question, I need to first gather current macroeconomic data relevant to the global technology and automotive sectors, including trends that may impact Tesla and Apple.\n"
    '1. macro_data_retriever("What are the current global macroeconomic trends, such as inflation rates, technological supply chain disruptions, and consumer spending, that affect the automotive and technology industries, particularly impacting Tesla and Apple?")\n'
    
    "Thought: With macroeconomic data in hand, I should gather recent stock performance and analysis for Tesla, including innovations and sustainability metrics.\n"
    '2. ticker_data_retriever("Tesla", "Analyze Tesla’s stock performance, recent technological advancements (such as electric vehicles and battery technology), and sustainability initiatives (like renewable energy and carbon reduction) in response to current macroeconomic trends.")\n'
    
    "Thought: Next, I will retrieve similar stock performance and company-specific data for Apple, focusing on its major innovations and sustainability efforts.\n"
    '3. ticker_data_retriever("Apple", "Examine Apple’s stock performance, major innovations (such as hardware and software improvements), and sustainability projects (e.g., carbon-neutral goals and resource recycling) in relation to current economic conditions.")\n'
    
    "Thought: To understand the financial health of Tesla and Apple, I will retrieve fundamental data that provides deeper insights into their balance sheets, income, and key financial ratios.\n"
    '4. fundamental_data_retriever("Tesla", "Provide detailed fundamental data, including net profit, total assets, cash flow, and profitability ratios.")\n'
    '5. fundamental_data_retriever("Apple", "Extract detailed financial fundamentals, such as total revenue, net profit margin, operational costs, and key financial ratios.")\n'
    
    "Thought: To complement the analysis, I will search for recent news articles that provide real-time insights and updates on Tesla and Apple.\n"
    '6. search("Recent news on Tesla stock performance, technological advancements, and sustainability efforts")\n'
    '7. search("Recent news on Apple stock performance, major technological updates, and sustainability strategies")\n'
    
    "Thought: To effectively visualize and present the analysis, I should create a series of charts summarizing key financial and performance data for both companies.\n"
    '8. data_visualization_tool($4, "line", "Tesla Stock Performance Over 4 Years", "Year", "Stock Price (USD)")\n'
    '9. data_visualization_tool($5, "line", "Apple Stock Performance Over 4 Years", "Year", "Stock Price (USD)")\n'
    
    "Thought: I have now gathered relevant macroeconomic, company-specific, and financial data, as well as visual representations of key trends for Tesla and Apple.\n"
    f"10. join() {END_OF_PLAN}\n"
)

OUTPUT_PROMPT = (
    "Solve a question answering task. Here are some guidelines:\n"
    "- In the Assistant Scratchpad, you will be given results of a plan you have executed to answer the user's question.\n"
    "- Thought needs to reason about the question based on the Observations in 1-2 sentences.\n"
    "- Ignore irrelevant action results.\n"
    "- If the required information is present, provide an informative, complete, and helpful answer to the user's question. Include as much relevant information as possible from the observations.\n"
    "- Use placeholders such as graph(8) on a new line to indicate generated graphs.\n"
    "- Do not use placeholders for tables. Directly include the table in the final answer from observations"
    "- If the user query is simple greetings respond appropriately."
    "- If you are unable to provide a satisfactory final answer, replan to get the required information. Respond in the following format:\n"
    "Thought: <reason about the task results and whether you have sufficient information to answer the question>\n"
    "Action: <action to take>\n"
    "Available actions:\n"
    f"(1) {JOINNER_FINISH}(the final answer to return to the user. Ensure the final answer is as detailed as possible, including all relevant explanations froms observations and using graph placeholders where applicable): returns the answer and finishes the task.\n"
    f"(2) {JOINNER_REPLAN}(the reasoning and other information to help plan again. Can be of any length): instructs why a replan is needed.\n"
    "\n"
    "Note: Never introduce new actions other than the ones provided above.\n"
    "Here are some examples:\n"
    "\n"
    "Question: Provide a detailed analysis of the current macroeconomic conditions affecting Tesla and Apple, highlighting their stock performance, financial fundamentals, and key factors influencing their technological and sustainability efforts.\n"
    'macro_data_retriever("What are the current global macroeconomic trends, such as inflation rates, technological supply chain disruptions, and consumer spending, that affect the automotive and technology industries, particularly impacting Tesla and Apple?")\n'
    """Observation: Rising inflation rates and tightening monetary policies have led to decreased consumer demand, affecting both the automotive and technology sectors. Supply chain constraints, particularly in semiconductors, continue to create challenges for companies like Tesla and Apple.\n"""
    'ticker_data_retriever("Tesla", "Analyze Tesla’s stock performance, recent technological advancements (such as electric vehicles and battery technology), and sustainability initiatives (like renewable energy and carbon reduction) in response to current macroeconomic trends.")\n'
    """Observation: Tesla’s stock has experienced fluctuations due to regulatory pressures and market corrections but has been buoyed by consistent demand for its electric vehicles and new renewable energy initiatives.\n"""
    'ticker_data_retriever("Apple", "Examine Apple’s stock performance, major innovations (such as hardware and software improvements), and sustainability projects (e.g., carbon-neutral goals and resource recycling) in relation to current economic conditions.")\n'
    """Observation: Apple’s stock has shown resilience, supported by its innovation in AI and wearables, along with its commitment to sustainability and carbon-neutral efforts.\n"""
    'fundamental_data_retriever("Tesla", "Provide detailed fundamental data, including net profit, total assets, cash flow, and profitability ratios.")\n'
    """Observation: Tesla's financial fundamentals show a solid net profit increase over the past three years, with significant investments in new technology and R&D.\n"""
    'fundamental_data_retriever("Apple", "Extract detailed financial fundamentals, such as total revenue, net profit margin, operational costs, and key financial ratios.")\n'
    """Observation: Apple's revenue and net profit margins remain strong, supported by a diversified product portfolio and strategic market expansion.\n"""
    'data_visualization_tool($4, "line", "Tesla Stock Performance Over 4 Years", "Year", "Stock Price (USD)")\n'
    """Observation: ('graph(4)', 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgA') \n"""
    'data_visualization_tool($5, "bar", "Comparative Financial Data of Tesla and Apple", "Category", "USD (in billions)")\n'
    """Observation: ('graph(5)', 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgA') \n"""
    "Thought: The gathered observations include comprehensive macroeconomic, stock performance, and fundamental data for Tesla and Apple. The generated graphs provide valuable visual insights.\n"
    f"Action: {JOINNER_FINISH}(Both Tesla and Apple face macroeconomic challenges such as inflation and supply chain disruptions. Tesla’s growth is driven by its innovative EV and battery technology, supported by renewable energy expansion. Meanwhile, Apple benefits from its strong financial health and innovation in products like wearables and AI. Their strategic sustainability initiatives position both companies to adapt to future market trends.\n"
    "\n"
    "Graph for Tesla's stock performance over 4 years:\n"
    "graph(8)\n"
    "\n"
    "Comparative financial data of Tesla and Apple:\n"
    "graph(9)\n"
    ")"
    "###\n"
    "\n"
)

CODE_OUTPUT_PROMPT = (
    "You are reviewing the results of a code-modification plan. Each Observation\n"
    "comes from a coding agent and is rendered in this format:\n"
    "  [<exit_status>] <one-line summary>\n"
    "  branch=<name> commits=<abbrev,...> files=<count>\n"
    "  --- diff ---\n"
    "  <unified diff, possibly truncated>\n"
    "\n"
    "Guidelines:\n"
    "- exit_status of 'error' or 'timeout' is a Replan signal: explain which\n"
    "  task failed and what needs to change before retrying.\n"
    "- 'no_changes' means the agent decided no edit was needed; that may be\n"
    "  legitimate. Use the summary to judge.\n"
    "- Use the diff to verify the change actually addresses the user's goal.\n"
    "  Do not invent code or file paths that aren't in the diff.\n"
    "- Reference the branch and the commit SHAs in your final answer so the\n"
    "  user can find their changes.\n"
    "- For multi-step plans, the workspace is shared: later observations show\n"
    "  cumulative diffs. Per-agent attribution is in the 'commits' field.\n"
    "- If conflicting agents touched the same file, the second commit may\n"
    "  show a partial revert; that is a Replan signal, not an answer.\n"
    "Available actions:\n"
    f"(1) {JOINNER_FINISH}(<concise summary describing what was changed, on\n"
    "    which branch, and why it satisfies the question. Mention the commit\n"
    "    SHAs and the files touched. Do not paste the diff verbatim.>)\n"
    f"(2) {JOINNER_REPLAN}(<concrete reason: which task index failed, what is\n"
    "    missing from the diff, what the next plan should do differently>)\n"
    "\n"
    "Note: Never introduce new actions other than the ones provided above.\n"
)


MULTI_WS_OUTPUT_PROMPT = (
    "You are reviewing the results of a multi-workspace plan. The plan was\n"
    "split into named workspace groups (e.g. `wsA`, `wsB`); each group ran\n"
    "in its own git branch. The Assistant Scratchpad shows the agent\n"
    "observations, with `branch=` and `commits=` lines tying each result\n"
    "back to its workspace.\n"
    "\n"
    "Before your `Action:` line, emit one verdict line per workspace, in\n"
    "this exact format:\n"
    "  Verdict (wsA): ok\n"
    "  Verdict (wsB): needs_replan: <one-line reason>\n"
    "Verdicts must use ONLY `ok` or `needs_replan: <reason>`. They live in\n"
    "the `Thought:` block; the engine reads them from there.\n"
    "\n"
    "If a CONFLICTS: section appears in the scratchpad, treat it as ground\n"
    "truth. Conflicting branch pairs SHOULD trigger a replan unless one\n"
    "branch is unambiguously authoritative for the conflicting files.\n"
    "\n"
    "Available actions:\n"
    f"(1) {JOINNER_FINISH}(<concise prose summary of what each workspace\n"
    "    delivered, then a fenced ```json block with the merge plan in this\n"
    "    exact shape:\n"
    "    ```json\n"
    "    {\n"
    "      \"keep\": [\"wsA\", \"wsB\"],\n"
    "      \"archive\": [],\n"
    "      \"merge_order\": [\"wsA\", \"wsB\"],\n"
    "      \"notes\": \"wsA implements; wsB documents; merge wsA first.\"\n"
    "    }\n"
    "    ```\n"
    "    >): final answer when every verdict is `ok`. Do not paste full\n"
    "    diffs into the answer; reference branches and commit SHAs instead.\n"
    f"(2) {JOINNER_REPLAN}(<concrete reason: which workspace(s) failed,\n"
    "    what the next plan should do differently. Mention the workspace\n"
    "    name and the specific files/branches involved.>): instructs why a\n"
    "    replan is needed.\n"
    "\n"
    "Note: Never introduce new actions other than the ones provided above.\n"
)


TABLE_OUTPUT_PROMPT = (
    "Solve a question answering task for multiple tickers. Here are some guidelines:\n"
    "- In the Assistant Scratchpad, you will be given results of a plan you have executed to answer the user's question for multiple tickers.\n"
    "- Thought needs to reason about the question based on the Observations in 1-2 sentences.\n"
    "- Ignore irrelevant action results.\n"
    "- Your goal is to provide a concise, tabular analysis where each ticker has a short, precise answer.\n"
    "- For EACH ticker mentioned in the query, provide a brief 1-sentence answer that directly addresses the question.\n"
    "- Format your answer as a JSON object where:\n"
    "  - Keys are the ticker symbols (e.g., 'AAPL', 'MSFT')\n"
    "  - Values are the corresponding 1-sentence answers\n"
    "- Keep each answer focused only on the most relevant information for that ticker.\n"
    "- Do not include any additional explanations, introductions, or conclusions outside the JSON structure.\n"
    "- Do not use placeholders for graphs or tables inside the JSON values.\n"
    "- If you are unable to provide information for any ticker, include it with a brief explanation of why.\n"
    "- If you are unable to provide a satisfactory final answer, replan to get the required information. Respond in the following format:\n"
    "\n"
    "Thought: <reason about the task results and whether you have sufficient information to answer the question>\n"
    "Action: <action to take>\n"
    "Available actions:\n"
    f"(1) {JOINNER_FINISH}(the final answer in JSON format as described above): returns the answer and finishes the task.\n"
    f"(2) {JOINNER_REPLAN}(the reasoning and other information to help plan again): instructs why a replan is needed.\n"
    "\n"
    "Note: Never introduce new actions other than the ones provided above.\n"
    "Example JSON output format: {{'AAPL': 'Revenue grew 8% YoY driven by services growth and strong iPhone demand.', 'MSFT': 'Cloud segment showed 15% growth with Azure leading the expansion efforts.'}}\n"
    "\n"
    "Here's an example:\n"
    "\n"
    "Question: Analyze the revenue growth trends for the following tickers: AAPL, MSFT, GOOG.\n"
    'fundamental_data_retriever("AAPL", "Retrieve revenue growth data for the past 3 years")\n'
    """Observation: Apple has shown a 3-year CAGR of 8.2% in revenue, with services growing at 15.6% and wearables at 12.3%, offsetting slower iPhone growth of 5.1%.\n"""
    'fundamental_data_retriever("MSFT", "Examine revenue trends across segments")\n'
    """Observation: Microsoft demonstrated a 3-year revenue CAGR of 16.2%, with Azure cloud services growing at 28% and commercial Office products at 12.5%.\n"""
    'fundamental_data_retriever("GOOG", "Get revenue breakdown and growth rates")\n'
    """Observation: Alphabet reported a 3-year revenue CAGR of 17.5%, with YouTube ads growing at 25%, Search at 16.3%, and Google Cloud at 31.2%.\n"""
    "Thought: I have sufficient data on revenue growth trends for all three companies with specific growth rates for different business segments.\n"
    f"Action: {JOINNER_FINISH}({{'AAPL': 'Revenue grew at 8.2% CAGR over 3 years, with services (15.6%) and wearables (12.3%) growing faster than iPhone (5.1%).', 'MSFT': 'Strong 16.2% revenue CAGR over 3 years led by Azure cloud (28%) and commercial Office products (12.5%).', 'GOOG': 'Impressive 17.5% revenue CAGR with YouTube ads (25%), Search (16.3%), and Google Cloud (31.2%) all showing strong growth.'}})\n"
    "###\n"
)

NO_ANWER_REPLY = '''I'm sorry, but I don't have enough information to provide a meaningful response to your question at this time.
This may be due to limitations in the available data, lack of context, or the specificity of the request. If you could provide more details, clarify the question, or refine the data uploaded, I’d be happy to try again.
Thank you for your understanding!'''
