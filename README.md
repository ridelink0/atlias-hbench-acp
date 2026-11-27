# atlias-hbench-acp

A thin [ACP](https://agentclientprotocol.com) agent that runs [Atlias](https://atlias.ridelinkmaps.com) on
[hbench](https://nodeq.tech) tasks.

Atlias is closed source and is not in this repository. hbench installs this agent inside each task's container. For each
prompt the agent:

1. packs the task workspace and sends it, with the prompt, to an Atlias bridge on the benchmark operator's machine
   (`ATLIAS_BRIDGE_URL`, default `http://host.docker.internal:18788/run`, authenticated with `ATLIAS_BRIDGE_TOKEN`);
2. lets Atlias work on that copy (Claude Haiku 5.5, low effort);
3. writes the files Atlias created or changed back into the workspace and removes the files it deleted.

hbench then runs the task's own tests in the container, unchanged.

**Token accounting.** This agent makes no model calls of its own. The `usage` in its ACP `PromptResponse` is the sum of
Atlias's per-call usage as returned by the model provider: uncached input, cache reads, cache writes and output. The
tokens hbench records are therefore exactly Atlias's.

**Model access.** Atlias reaches Claude through the operator's Claude subscription via the Claude Agent SDK, not an API
key, so no `--ae` key is passed to hbench.
