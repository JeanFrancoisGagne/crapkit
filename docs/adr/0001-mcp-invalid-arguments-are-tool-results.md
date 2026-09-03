---
status: accepted
---

# Invalid MCP tool arguments answer as a tool result, not a protocol error

The MCP specification's example lists invalid arguments under the JSON-RPC protocol error `-32602`, and crapkit's server already answered an unknown tool and a missing configuration as a tool result with `isError` true. When 0.5.0 added argument validation (a missing positional, an undeclared key, a wrong type), we kept the house precedent: the answer is a tool result whose text is written in the tool's own vocabulary, such as `brief needs name (see inputSchema.required)`, and the session continues. The reason is the reader: a coding agent reads tool results and corrects its next call, while many clients surface protocol errors as a transport failure the agent never sees. Protocol errors stay reserved for malformed JSON-RPC (an unparsable frame, an unknown method, an exception escaping the server), where there is no tool to speak for.

Consequence: a client that filters on `-32602` to detect bad arguments will not see crapkit's refusals; it reads `isError` instead, which is also how it must read the two older refusals.
