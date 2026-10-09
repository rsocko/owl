import { createServer } from "node:http";
import { createCanvas, CanvasError, joinSession } from "@github/copilot-sdk/extension";
import { graph } from "./graph-data.mjs";
import { renderHtml } from "./renderer.mjs";

const servers = new Map();

function sendJson(response, status, body) {
    response.writeHead(status, {
        "Content-Type": "application/json; charset=utf-8",
        "Cache-Control": "no-store",
    });
    response.end(JSON.stringify(body));
}

function broadcast(entry, eventName, data) {
    const message = `event: ${eventName}\ndata: ${JSON.stringify(data)}\n\n`;
    for (const response of entry.clients) response.write(message);
}

function routeRequest(entry, request, response) {
    const url = new URL(request.url ?? "/", "http://127.0.0.1");

    if (request.method === "GET" && url.pathname === "/") {
        response.writeHead(200, {
            "Content-Type": "text/html; charset=utf-8",
            "Cache-Control": "no-store",
        });
        response.end(renderHtml());
        return;
    }

    if (request.method === "GET" && url.pathname === "/api/graph") {
        sendJson(response, 200, graph);
        return;
    }

    if (request.method === "GET" && url.pathname === "/events") {
        response.writeHead(200, {
            "Content-Type": "text/event-stream",
            "Cache-Control": "no-cache",
            Connection: "keep-alive",
        });
        response.write(": connected\n\n");
        entry.clients.add(response);
        request.on("close", () => entry.clients.delete(response));
        return;
    }

    sendJson(response, 404, { error: "Not found" });
}

async function startServer(instanceId) {
    const entry = { server: undefined, clients: new Set(), url: "" };
    const server = createServer((request, response) => {
        try {
            routeRequest(entry, request, response);
        } catch (error) {
            const message = error instanceof Error ? error.message : "Unexpected canvas server error";
            if (!response.headersSent) sendJson(response, 500, { error: message });
            else response.end();
        }
    });
    entry.server = server;
    await new Promise((resolve, reject) => {
        server.once("error", reject);
        server.listen(0, "127.0.0.1", resolve);
    });
    const address = server.address();
    const port = typeof address === "object" && address ? address.port : 0;
    entry.url = `http://127.0.0.1:${port}/`;
    servers.set(instanceId, entry);
    return entry;
}

const nodeById = new Map(graph.nodes.map((node) => [node.id, node]));

const session = await joinSession({
    canvases: [
        createCanvas({
            id: "owl-architecture",
            displayName: "OWL System Map",
            description: "Explore OWL's current and planned capabilities, architecture, domain entities, storage ownership, and cross-system relationships.",
            inputSchema: {
                type: "object",
                properties: {
                    lens: { type: "string", enum: ["feature", "architecture", "entity"] },
                    focusNodeId: { type: "string" },
                },
                additionalProperties: false,
            },
            actions: [
                {
                    name: "focus_node",
                    description: "Focus a capability, component, entity, or store in an open OWL system map.",
                    inputSchema: {
                        type: "object",
                        properties: { nodeId: { type: "string" } },
                        required: ["nodeId"],
                        additionalProperties: false,
                    },
                    handler: (ctx) => {
                        if (!nodeById.has(ctx.input.nodeId)) {
                            throw new CanvasError("node_not_found", `Unknown node: ${ctx.input.nodeId}`);
                        }
                        const entry = servers.get(ctx.instanceId);
                        if (!entry) {
                            throw new CanvasError("canvas_not_open", "The canvas instance is not open.");
                        }
                        broadcast(entry, "focus", { nodeId: ctx.input.nodeId });
                        return { focused: ctx.input.nodeId };
                    },
                },
                {
                    name: "get_map_summary",
                    description: "Return OWL map counts grouped by lens, status, kind, and owning system.",
                    handler: () => {
                        const countBy = (field) => Object.fromEntries(
                            [...new Set(graph.nodes.map((node) => node[field]).filter(Boolean))]
                                .map((value) => [value, graph.nodes.filter((node) => node[field] === value).length]),
                        );
                        const systems = {};
                        for (const node of graph.nodes) {
                            for (const store of node.stores ?? []) {
                                systems[store.system] = (systems[store.system] ?? 0) + 1;
                            }
                        }
                        return {
                            nodes: graph.nodes.length,
                            relationships: graph.edges.length,
                            lenses: countBy("lens"),
                            statuses: countBy("status"),
                            kinds: countBy("kind"),
                            systems,
                            assessedAt: graph.assessedAt,
                        };
                    },
                },
                {
                    name: "get_entity_definition",
                    description: "Return the definition, ownership, storage, source paths, and relationships for one mapped OWL entity.",
                    inputSchema: {
                        type: "object",
                        properties: { nodeId: { type: "string" } },
                        required: ["nodeId"],
                        additionalProperties: false,
                    },
                    handler: (ctx) => {
                        const node = nodeById.get(ctx.input.nodeId);
                        if (!node || !["entity", "store", "external"].includes(node.kind)) {
                            throw new CanvasError("entity_not_found", `Unknown entity or store: ${ctx.input.nodeId}`);
                        }
                        return {
                            ...node,
                            relationships: graph.edges.filter(
                                (edge) => edge.source === node.id || edge.target === node.id,
                            ),
                        };
                    },
                },
            ],
            open: async (ctx) => {
                const entry = servers.get(ctx.instanceId) ?? await startServer(ctx.instanceId);
                const input = ctx.input ?? {};
                const query = new URLSearchParams();
                if (input.lens) query.set("lens", input.lens);
                if (input.focusNodeId) query.set("focus", input.focusNodeId);
                return {
                    title: "OWL System Map",
                    status: `Current and planned state assessed ${graph.assessedAt}`,
                    url: `${entry.url}${query.size ? `?${query}` : ""}`,
                };
            },
            onClose: async (ctx) => {
                const entry = servers.get(ctx.instanceId);
                if (!entry) return;
                servers.delete(ctx.instanceId);
                for (const response of entry.clients) response.end();
                await new Promise((resolve) => entry.server.close(resolve));
            },
        }),
    ],
});

session.log("OWL system map canvas loaded.", { level: "info", ephemeral: true });
