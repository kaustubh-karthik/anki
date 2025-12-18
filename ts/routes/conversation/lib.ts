// Copyright: Ankitects Pty Ltd and contributors
// License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

export type ConversationTurnResponse =
    | { ok: true; response: { assistant_reply_ko: string; follow_up_question_ko: string } }
    | { ok: false; error: string };

export function buildConversationCommand(
    kind:
        | "init"
        | "start"
        | "turn"
        | "gloss"
        | "event"
        | "wrap"
        | "apply_suggestions"
        | "plan_reply",
    payload?: unknown,
): string {
    if (kind === "init") {
        return "conversation:init";
    }
    if (kind === "gloss") {
        const lexeme = (payload as any)?.lexeme;
        if (typeof lexeme !== "string") {
            throw new Error("lexeme required");
        }
        return `conversation:gloss:${lexeme}`;
    }
    if (kind === "wrap") {
        return "conversation:wrap";
    }
    if (kind === "apply_suggestions") {
        const json = payload ? JSON.stringify(payload) : "{}";
        return `conversation:apply_suggestions:${json}`;
    }
    if (kind === "plan_reply") {
        const json = payload ? JSON.stringify(payload) : "{}";
        return `conversation:plan_reply:${json}`;
    }
    const json = payload ? JSON.stringify(payload) : "{}";
    return `conversation:${kind}:${json}`;
}
