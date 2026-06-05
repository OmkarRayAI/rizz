
default_query_understanding="Brief analysis of what the user is asking about"
default_temporal_context="Specify the date/time period for analysis"
default_research_approach="High-level strategic guidance on what information to gather and what analyses to perform"
default_dos="""
✓ "Utilize economic indicators data related to steel consumption"
✓ "Analyze quarterly revenue trends for the company"
✓ "Consider comparing with industry peers"
"""

default_donts="""
✗ "economic_indicators_retriever('What are the monthly steel consumption values in MMT?')"
✗ "data_visualization_tool('Create a line chart showing...')"
✗ Specific code snippets or API call formats
"""
default_meta_example="""
When you see a query like "List the repositories i have starred currently", you should:**
1. Understand this is a simple GitHub query
2. Create a simple meta plan that mentions using GitHub tools to retrieve starred repositories
3. Do NOT try to call any tools yourself
4. Just provide the strategic guidance
5. DO NOT emit a WORKSPACE_TOPOLOGY block for this — it's a single-track query.

When you see a query like "Add a /login route returning 401 on bad creds, plus
a pytest covering it, and update API.md with the new endpoint", the plan has two
independent coding tracks (impl+test on one branch, docs on another). Emit:

WORKSPACE_TOPOLOGY:
  wsA: 1, 2
  wsB: 3
  default: wsA

so impl+test live on wsA and docs lives on wsB. Tasks 1+2 share files (route
implementation and its test); task 3 only touches API.md and is independent.

"""
