## Function requirement

- 1. auto get infra topology from azure, identify the services in azure aks/service fabric, save infra topology to local as analysis workflow context 2. reuse current connectors if possible 3.  connector as AI agent tool
- 1. auto vpc/vms/gke/loadbalancer infra topology from gcp, identify the services in gcp gke/service fabric, save infra topology to local as analysis workflow context 2. reuse current connectors if possible 3.  connector as AI agent tool
  - get vpc/vms/gke/loadbalancer infra topology from gcp
- enable AI agent tool: https://learn.microsoft.com/en-us/agent-framework/agents/tools/?pivots=programming-language-python
- enable AI agent MCP, so that I can connect to ADO/ES or other MCP: 
  https://learn.microsoft.com/en-us/agent-framework/agents/tools/hosted-mcp-tools?pivots=programming-language-python 
  https://learn.microsoft.com/en-us/agent-framework/agents/tools/local-mcp-tools?pivots=programming-language-python


- enable AI agent skill, so that I can use ES skill: https://github.com/elastic/agent-skills
  -  https://learn.microsoft.com/en-us/agent-framework/agents/skills?pivots=programming-language-python
- enable RAG: https://learn.microsoft.com/en-us/agent-framework/agents/rag?pivots=programming-language-python
- enable multi-turn conversations: https://learn.microsoft.com/en-us/agent-framework/get-started/multi-turn?pivots=programming-language-python
- enable memory & Persistence: https://learn.microsoft.com/en-us/agent-framework/get-started/memory?pivots=programming-language-python
- run whole solution without client.agents.create_version and real AI capacity
- Add a chat box to chat with AI
- Add a button to index the RAG documents
- Add a button to trigger patrol and Auto find issues from ELK or Infra topology
- Use AgentSession to keep conversation context between invocations, https://learn.microsoft.com/en-us/agent-framework/agents/conversations/?pivots=programming-language-python
- Use InMemoryHistoryProvider: https://learn.microsoft.com/en-us/agent-framework/agents/conversations/context-providers?pivots=programming-language-python
- auto find all subscription name/id or get subscription name/id from user chat in agent.py

## Non-function requirement:

<!--
run app with AZURE_OPENAI_ENDPOINT/AZURE_OPENAI_API_KEY but without client.agents.create_version
So the user wants to:
1. Use AZURE_OPENAI_ENDPOINT and AZURE_OPENAI_API_KEY directly (standard Azure OpenAI API, not Foundry Project endpoint)
2. Not use client.agents.create_version from the Azure AI Project SDK
 -->

<!-- ## Function requirement
A sre agent like https://sre.azure.com but with more:
1. can inspect log/trace/metric  from es and auto find abnormal message
2. auto get infra from azure/gke and identify the services in azure/gke k8s/service fabric 
3. generate playbook with automation scripts to fix it
4. SSO
5. Use Microsoft Agent Framework and support AI skills/Tools/MCP
6. Enable all tools in https://learn.microsoft.com/en-us/agent-framework/
7. hourly-patrol-es-inspection as AI agent tool
8. workflow to compare 2 DrillPlan environment
9. workflow to submit error log, auto analysis, generate playbook to fix
10. workflow to auto detect issue, auto analysis and generate playbook to fix
11. workflow: search RAG documents, inspect current infrastructure to generate new infrastructure release playbook (e.g.: for tj06)
12. extending i18n to system_prompts.py as discussed earlier
13. use skill from https://github.com/elastic/agent-skills


## Non-function requirement:
- use uv to manage virtual environment and package
- don't use [huggingface](https://huggingface.co)
- be abel to use custom endpoint for any AI
- always brainstorming before implementation
- use typescript for frontend
- deploy to k8s
- enble i18n
- create dev branch for each requirements -->
