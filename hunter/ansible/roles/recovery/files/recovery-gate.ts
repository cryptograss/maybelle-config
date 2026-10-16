/**
 * recovery-gate: what Pi may do on hunter's host without asking Justin.
 *
 * Pi asks for nothing by default ("it does not ask for approval before every
 * tool call", its security doc), and recovery@hunter is root in all but name
 * (the docker group). So every tool call comes through here first:
 *
 *   deny   never, whatever anyone says: the BuildKit calls that crashed
 *          dockerd on 2026-10-15, anything that deletes containers, images,
 *          volumes or networks, rebooting. Asking doesn't unlock these.
 *   allow  looking: reading files, listing, searching, the memory tools, and
 *          shell commands made only of read-only programs.
 *   ask    everything else: Justin sees the call and says yes or no. With no
 *          one to ask (no terminal), it's refused.
 *
 * The decision is `decide` alone, a plain function, tested by
 * ../test_recovery_gate.mjs (Node's official builds: node --experimental-strip-types
 * --test; or through jiti, as Pi loads this file).
 */

import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";

export type Verdict = { verdict: "allow" | "ask" | "deny"; why: string };

// Anywhere in a command line, however it's wrapped.
const DENY: [RegExp, string][] = [
	[/\bdocker\s+(buildx|builder)\b/, "BuildKit calls crashed dockerd on 2026-10-15"],
	[/\bdocker\s+(system|container|image|volume|network|builder)\s+prune\b/, "pruning deletes"],
	[/\bdocker\s+(rm|rmi)\b/, "removing containers or images deletes"],
	[/\bdocker\s+(container|image|volume|network)\s+(rm|remove)\b/, "removing deletes"],
	[/\bdocker\s+volume\s+(rm|prune)\b/, "removing volumes deletes data"],
	[/\bdocker[\s-]+compose\b.*\bdown\b/, "compose down removes containers"],
	// As a command, not as a word in a search: `grep -i reboot` only looks.
	[/(^|[;&|(]\s*|\bsudo\s+)(reboot|shutdown|poweroff|halt)\b/, "rebooting hunter is Justin's call and his hand"],
	[/\bsystemctl\s+(reboot|poweroff|halt|kexec)\b/, "rebooting hunter is Justin's call and his hand"],
	[/\bmkfs(\.\w+)?\b|\bdd\s+[^|]*\bof=\/dev\//, "writes to a disk"],
	[/\brm\s+(-\w*\s+)*\/(opt\/magenta|var\/lib\/docker|var\/lib\/containerd|etc)\b/, "deletes the server's own files"],
];

// Programs that only look. A command line is allowed without asking only if
// every piece of a pipeline is one of these, and it has nothing else in it:
// no ; && || & > < $( ` -- no redirection, substitution or second command.
const LOOKERS = new Set([
	"uptime", "df", "free", "cat", "head", "tail", "less", "grep", "egrep", "wc", "ls", "stat", "du",
	"ps", "uname", "date", "whoami", "id", "hostname", "journalctl", "sort", "uniq", "cut", "awk",
	"jq", "nproc", "lsblk", "findmnt", "ss", "ip", "dig", "getent", "pwd", "echo", "which", "file",
]);
const READONLY_SUBCOMMANDS: Record<string, Set<string>> = {
	systemctl: new Set(["status", "is-active", "is-failed", "is-enabled", "list-units", "list-timers", "show", "cat"]),
	docker: new Set(["ps", "logs", "images", "info", "version", "top", "stats", "port", "df"]),
	git: new Set(["status", "log", "show", "diff", "branch", "remote"]),
};
// `docker system df`, `docker container ls`, `docker image ls`, `docker volume ls`, `docker network ls`
const DOCKER_LISTS = /^docker\s+((system\s+df|(container|image|volume|network)\s+(ls|list))\b|stats\s+--no-stream\b)/;
// awk can run commands (system(), print | "cmd"): only without them.
const AWK_ESCAPES = /system\s*\(|\|\s*"|getline/;

function looksOnly(piece: string): boolean {
	// `timeout 20 docker ps`: judge what it runs.
	const bare = piece.trim().replace(/^timeout\s+(-\S+\s+)*\d+[smh]?\s+/, "");
	if (bare !== piece.trim()) return looksOnly(bare);
	const words = piece.trim().split(/\s+/);
	const program = words[0];
	if (!program) return false;
	if (program === "awk" && AWK_ESCAPES.test(piece)) return false;
	if (program === "tail" && words.includes("-f")) return true; // still only looking
	if (program === "docker" && DOCKER_LISTS.test(piece.trim())) return true;
	if (program === "docker" && words[1] === "stats" && !words.includes("--no-stream")) return false;
	const subcommands = READONLY_SUBCOMMANDS[program];
	if (subcommands) return subcommands.has(words[1] ?? "");
	if (program === "curl") {
		// Memory Lane's read API on maybelle, GET only.
		return /^curl\s+(-s\s+|-sS\s+|--silent\s+)*'?https?:\/\/10\.0\.0\.2:3000\/api\/[^\s']*'?\s*$/.test(piece.trim());
	}
	return LOOKERS.has(program);
}

// Where the keys are: looking there is not just looking.
const SECRETS = /\.config\/magenta|\.credentials|\.env\b|\/etc\/shadow|vault|_key\b|token/i;

export function decide(toolName: string, input: Record<string, unknown>): Verdict {
	if (SECRETS.test(JSON.stringify(input))) {
		const command = String(input.command ?? "");
		for (const [pattern, why] of DENY) {
			if (pattern.test(command)) return { verdict: "deny", why };
		}
		return { verdict: "ask", why: "it touches where the keys are" };
	}
	if (toolName === "bash") {
		const command = String(input.command ?? "");
		for (const [pattern, why] of DENY) {
			if (pattern.test(command)) return { verdict: "deny", why };
		}
		if (/[;&<>`]|\$\(|\n/.test(command)) {
			return { verdict: "ask", why: "more than one command, a redirection or a substitution" };
		}
		const pieces = command.split("|");
		if (pieces.every(looksOnly)) return { verdict: "allow", why: "read-only" };
		return { verdict: "ask", why: "may change something" };
	}
	if (["read", "grep", "find", "ls"].includes(toolName)) return { verdict: "allow", why: "read-only" };
	if (toolName.startsWith("mcp__magenta_memory_v2__") || toolName === "tool_search") {
		return { verdict: "allow", why: "the record, read-only" };
	}
	return { verdict: "ask", why: `${toolName} may change something` };
}

export default function (pi: ExtensionAPI) {
	pi.on("tool_call", async (event, ctx) => {
		const input = (event.input ?? {}) as Record<string, unknown>;
		const { verdict, why } = decide(event.toolName, input);
		if (verdict === "allow") return undefined;
		if (verdict === "deny") return { block: true, reason: `Refused on hunter's host: ${why}.` };
		if (!ctx.hasUI) return { block: true, reason: `Refused: no one to ask (${why}).` };
		const shown = event.toolName === "bash" ? String(input.command ?? "") : JSON.stringify(input).slice(0, 600);
		const choice = await ctx.ui.select(`magent wants to (${why}):\n\n  ${event.toolName}: ${shown}\n\nAllow?`, ["Yes", "No"]);
		return choice === "Yes" ? undefined : { block: true, reason: "Justin said no." };
	});
}
