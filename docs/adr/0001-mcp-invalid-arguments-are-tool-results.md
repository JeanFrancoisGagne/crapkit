---
status: accepted
---

# Invalid MCP tool arguments answer as a tool result, not a protocol error

The MCP specification's example lists invalid arguments under the JSON-RPC protocol error `-32602`, and crapkit's server already answered an unknown tool and a missing configuration as a tool result with `isError` true. When 0.5.0 added argument validation (a missing positional, an undeclared key, a wrong type), we kept the house precedent: the answer is a tool result whose text is written in the tool's own vocabulary, such as `brief needs name (see inputSchema.required)`, and the session continues. The reason is the reader: a coding agent reads tool results and corrects its next call, while many clients surface protocol errors as a transport failure the agent never sees. Protocol errors stay reserved for malformed JSON-RPC (an unparsable frame, an unknown method, an exception escaping the server), where there is no tool to speak for.

Consequence: a client that filters on `-32602` to detect bad arguments will not see crapkit's refusals; it reads `isError` instead, which is also how it must read the two older refusals.

Amendment (0.8.1): one class of undeclared key is not refused. Gemini CLI 0.61.0 adds a `wait_for_previous` boolean to every MCP tool's input schema, tells its model to set it, reads it to order the calls of one turn, and forwards it with the call. Refusing it failed every Gemini call that carried it, and no edit on the model's side could fix that. Such keys belong to the client, so the server names them in `CLIENT_KEYS` and drops them before the tool's table checks the call; the CLI runs as it would without them. Every other undeclared key is still refused, and no served schema lists a client key.

Amendment (0.8.1): the line between the two kinds of answer is the JSON-RPC envelope. `arguments` that is not a JSON object (an array, a string, a number or a boolean) is a bad argument, so it answers a tool result that says to send an object; it used to reach checks that read it as one and answer `-32603`. `params` that is not a JSON object is malformed JSON-RPC, so it answers `-32602`, the code JSON-RPC gives invalid params, and never an internal error.
