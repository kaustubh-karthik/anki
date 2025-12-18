<script lang="ts">
    import { bridgeCommand, bridgeCommandsAvailable } from "@tslib/bridgecommand";
    import { buildConversationCommand, type ConversationTurnResponse } from "./lib";

    let started = false;
    let decksText = "Korean";
    let message = "";
    let transcript: Array<{ role: "user" | "assistant"; text: string }> = [];
    let lastGloss: string | null = null;
    let error: string | null = null;

    function start(): void {
        error = null;
        if (!bridgeCommandsAvailable()) {
            error = "Bridge commands not available.";
            return;
        }
        const decks = decksText
            .split(",")
            .map((s) => s.trim())
            .filter(Boolean);
        bridgeCommand(
            buildConversationCommand("start", { decks }),
            (resp: { ok: boolean; error?: string }) => {
                if (!resp?.ok) {
                    error = resp?.error ?? "Failed to start session.";
                    return;
                }
                started = true;
                transcript = [];
            },
        );
    }

    function send(): void {
        error = null;
        if (!started) {
            error = "Start a session first.";
            return;
        }
        const text = message.trim();
        if (!text) {
            return;
        }
        transcript = [...transcript, { role: "user", text }];
        message = "";
        bridgeCommand(
            buildConversationCommand("turn", { text_ko: text, confidence: null }),
            (resp: ConversationTurnResponse) => {
                if (!resp?.ok) {
                    error = resp?.error ?? "Turn failed.";
                    return;
                }
                transcript = [
                    ...transcript,
                    { role: "assistant", text: resp.response.assistant_reply_ko },
                    { role: "assistant", text: resp.response.follow_up_question_ko },
                ];
            },
        );
    }

    function gloss(lexeme: string): void {
        if (!bridgeCommandsAvailable()) {
            return;
        }
        bridgeCommand(buildConversationCommand("gloss", { lexeme }), (resp: any) => {
            if (resp?.found) {
                lastGloss = `${resp.lexeme}: ${resp.gloss ?? ""}`.trim();
            } else {
                lastGloss = null;
            }
        });
    }

    function logEvent(type: string, token?: string): void {
        if (!bridgeCommandsAvailable()) {
            return;
        }
        bridgeCommand(buildConversationCommand("event", { type, token }));
    }

    function refreshWrap(): void {
        if (!bridgeCommandsAvailable()) {
            return;
        }
        bridgeCommand(buildConversationCommand("wrap"), (resp: any) => {
            if (resp?.ok) {
                // show a minimal summary in the footer
                lastGloss = `reinforce: ${(resp.wrap?.reinforce ?? []).join(", ")}`;
            }
        });
    }
</script>

<div class="page">
    <h1>Conversation Practice</h1>
    {#if !bridgeCommandsAvailable()}
        <p>Not running inside Anki.</p>
    {/if}

    <div class="row">
        <label>Decks (comma-separated)</label>
        <input bind:value={decksText} />
        <button on:click={start}>Start</button>
    </div>

    <div class="chat">
        {#each transcript as line}
            <div class={`msg ${line.role}`}>
                {#each line.text.split(" ") as tok}
                    <span
                        class="tok"
                        on:mouseenter={() => gloss(tok)}
                        on:click={() => logEvent("dont_know", tok)}
                        on:dblclick={() => logEvent("practice_again", tok)}
                        >{tok}</span
                    >
                    <span> </span>
                {/each}
            </div>
        {/each}
    </div>

    <div class="row">
        <input
            placeholder="Type Korean…"
            bind:value={message}
            on:keydown={(e) => e.key === "Enter" && send()}
        />
        <button on:click={send}>Send</button>
    </div>

    {#if lastGloss}
        <div class="gloss">{lastGloss}</div>
    {/if}
    {#if error}
        <div class="error">{error}</div>
    {/if}

    <div class="row">
        <button on:click={refreshWrap}>Refresh Wrap</button>
    </div>
</div>

<style>
    .page {
        padding: 16px;
        font-family: system-ui, sans-serif;
    }
    .row {
        display: flex;
        gap: 8px;
        align-items: center;
        margin: 8px 0;
    }
    .chat {
        border: 1px solid #ddd;
        padding: 8px;
        min-height: 200px;
        margin: 12px 0;
    }
    .msg {
        margin: 6px 0;
        white-space: pre-wrap;
    }
    .msg.user {
        text-align: right;
    }
    .tok {
        cursor: help;
    }
    .gloss {
        margin-top: 8px;
        color: #333;
        font-size: 0.9em;
    }
    .error {
        margin-top: 8px;
        color: #b00020;
    }
</style>
