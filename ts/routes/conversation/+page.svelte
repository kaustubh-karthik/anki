<!--
Copyright: Ankitects Pty Ltd and contributors
License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html
-->
<script lang="ts">
    import { onMount } from "svelte";
    import { bridgeCommand, bridgeCommandsAvailable } from "@tslib/bridgecommand";
    import {
        buildConversationCommand,
        tokenizeForUi,
        type ConversationTurnResponse,
    } from "./lib";

    let started = false;
    let deckOptions: string[] = [];
    let selectedDecks: string[] = ["Korean"];
    let topicId = "room_objects";
    let message = "";
    type AssistantResponse = NonNullable<
        Extract<ConversationTurnResponse, { ok: true }>["response"]
    >;
    type Turn = {
        user_text_ko: string;
        assistant: AssistantResponse;
    };
    let turns: Turn[] = [];
    let showHintByTurn: Record<number, boolean> = {};
    let showExplainByTurn: Record<number, boolean> = {};
    let showTranslateByTurn: Record<number, boolean> = {};
    let translationByTurn: Record<number, string> = {};
    let error: string | null = null;
    let intentEn = "";
    let replyOptions: string[] = [];
    let applyDeck = "Korean";
    let showApplySuggestions = false;
    let lastWrap: any = null;
    let showPlanReply = false;
    let showSettings = false;
    let settings: any = null;
    let noGlossField = false;
    let lexemeFieldNamesText = "";
    let glossFieldNamesText = "";
    let showExportTelemetry = false;
    let telemetryJson = "";
    let inFlight = false;
    let lastJobDebug: string | null = null;
    let tooltip: { text: string; gloss: string; x: number; y: number } | null = null;
    let hoveredWordsThisTurn: Set<string> = new Set();

    function sleep(ms: number): Promise<void> {
        return new Promise((resolve) => setTimeout(resolve, ms));
    }

    function bridgeCommandPromise<T>(command: string): Promise<T> {
        return new Promise((resolve) =>
            bridgeCommand<T>(command, (resp) => resolve(resp)),
        );
    }

    async function waitForJob(jobId: string, timeoutMs = 600_000): Promise<any> {
        const startedAt = Date.now();
        for (;;) {
            const resp: any = await bridgeCommandPromise(
                buildConversationCommand("poll", { job_id: jobId }),
            );
            lastJobDebug = resp ? JSON.stringify(resp) : String(resp);
            if (!resp?.ok) {
                return resp;
            }
            if (resp.status === "done") {
                return resp.result;
            }
            if (Date.now() - startedAt > timeoutMs) {
                return { ok: false, error: "Timed out waiting for response." };
            }
            await sleep(250);
        }
    }

    onMount(() => {
        if (!bridgeCommandsAvailable()) {
            return;
        }
        bridgeCommand(buildConversationCommand("get_settings"), (resp: any) => {
            if (resp?.ok) {
                settings = resp.settings ?? null;
                noGlossField = settings?.gloss_field_index == null;
                lexemeFieldNamesText = Array.isArray(settings?.lexeme_field_names)
                    ? settings.lexeme_field_names.join(", ")
                    : "";
                glossFieldNamesText = Array.isArray(settings?.gloss_field_names)
                    ? settings.gloss_field_names.join(", ")
                    : "";
            }
        });
        bridgeCommand(buildConversationCommand("decks"), (resp: any) => {
            if (!resp?.ok || !Array.isArray(resp.decks)) {
                return;
            }
            deckOptions = resp.decks.filter((d: unknown) => typeof d === "string");
            if (!selectedDecks.length && deckOptions.length) {
                selectedDecks = [deckOptions[0]];
            }
            if (deckOptions.length && !deckOptions.includes(applyDeck)) {
                applyDeck = deckOptions[0];
            }
        });
    });

    function sendEvent(payload: Record<string, unknown>): void {
        if (!bridgeCommandsAvailable()) {
            return;
        }
        bridgeCommand(buildConversationCommand("event", payload));
    }

    function toggleByIndex(
        map: Record<number, boolean>,
        index: number,
    ): Record<number, boolean> {
        return { ...map, [index]: !map[index] };
    }

    function toggleTranslate(index: number, textKo: string): void {
        void (async () => {
            error = null;
            showTranslateByTurn = toggleByIndex(showTranslateByTurn, index);
            if (translationByTurn[index] || !showTranslateByTurn[index]) {
                return;
            }
            if (!bridgeCommandsAvailable()) {
                return;
            }
            if (inFlight) {
                error = "Busy.";
                return;
            }

            // Track that user needed to translate - mark all words as unknown
            const wordsInSentence = tokenizeForUi(textKo)
                .filter((tok) => tok.kind === "word")
                .map((tok) => tok.text);
            if (wordsInSentence.length > 0) {
                sendEvent({ type: "sentence_translated", tokens: wordsInSentence });
            }

            inFlight = true;
            try {
                const startResp: any = await bridgeCommandPromise(
                    buildConversationCommand("translate_async", { text_ko: textKo }),
                );
                if (!startResp?.ok) {
                    error = startResp?.error ?? "translate failed.";
                    return;
                }
                const result = await waitForJob(startResp.job_id);
                if (!result?.ok) {
                    error = result?.error ?? "translate failed.";
                    return;
                }
                translationByTurn = {
                    ...translationByTurn,
                    [index]: result.translation_en ?? "",
                };
            } finally {
                inFlight = false;
            }
        })();
    }

    function start(): void {
        error = null;
        if (!bridgeCommandsAvailable()) {
            error = "Bridge commands not available.";
            return;
        }
        if (settings?.provider === "fake") {
            error = "Provider is set to fake; choose local or openai in Settings.";
            return;
        }
        const decks = selectedDecks.filter(Boolean);
        if (!decks.length) {
            error = "Select at least one deck.";
            return;
        }
        bridgeCommand(
            buildConversationCommand("start", { decks, topic_id: topicId || null }),
            (resp: { ok: boolean; error?: string; llm_enabled?: boolean }) => {
                if (!resp?.ok) {
                    error = resp?.error ?? "Failed to start session.";
                    return;
                }
                if (resp.llm_enabled === false) {
                    error =
                        "LLM provider not configured (missing API key?). Check Settings and gpt-api.txt.";
                    started = false;
                    return;
                }
                started = true;
                turns = [];
                showHintByTurn = {};
                showExplainByTurn = {};
                showTranslateByTurn = {};
                translationByTurn = {};
            },
        );
    }

    function send(): void {
        void (async () => {
            error = null;
            if (!started) {
                error = "Start a session first.";
                return;
            }
            const text = message.trim();
            if (!text) {
                return;
            }
            if (!bridgeCommandsAvailable()) {
                return;
            }
            if (inFlight) {
                error = "Busy.";
                return;
            }

            // Track words from previous AI turns that the user understood (didn't hover)
            if (turns.length > 0) {
                const lastTurn = turns[turns.length - 1];
                const allWords = tokenizeForUi(lastTurn.assistant.assistant_reply_ko)
                    .filter((tok) => tok.kind === "word")
                    .map((tok) => tok.text);
                const knownWords = allWords.filter((w) => !hoveredWordsThisTurn.has(w));
                if (knownWords.length > 0) {
                    sendEvent({ type: "words_known", tokens: knownWords });
                }
            }
            hoveredWordsThisTurn = new Set();

            message = "";
            inFlight = true;
            try {
                const startResp: any = await bridgeCommandPromise(
                    buildConversationCommand("turn_async", {
                        text_ko: text,
                    }),
                );
                if (!startResp?.ok) {
                    error = startResp?.error ?? "Turn failed.";
                    return;
                }
                const result: ConversationTurnResponse = await waitForJob(
                    startResp.job_id,
                );
                if (!result?.ok) {
                    error = (result as any)?.error ?? "Turn failed.";
                    return;
                }
                turns = [
                    ...turns,
                    {
                        user_text_ko: text,
                        assistant: (result as any).response,
                    },
                ];
            } finally {
                inFlight = false;
            }
        })();
    }

    function showWordTooltip(
        word: string,
        glosses: Record<string, string> | undefined,
        event: MouseEvent,
    ): void {
        const gloss = glosses?.[word];
        if (gloss) {
            const rect = (event.target as HTMLElement).getBoundingClientRect();
            tooltip = {
                text: word,
                gloss,
                x: rect.left + window.scrollX,
                y: rect.bottom + window.scrollY + 4,
            };
        } else {
            tooltip = null;
        }
        // Track that the user hovered over this word (indicates they may not know it)
        hoveredWordsThisTurn.add(word);
        sendEvent({ type: "dont_know", token: word });
    }

    function hideTooltip(): void {
        tooltip = null;
    }

    function refreshWrap(): void {
        if (!bridgeCommandsAvailable()) {
            return;
        }
        bridgeCommand(buildConversationCommand("wrap"), (resp: any) => {
            if (resp?.ok) {
                lastWrap = resp.wrap ?? null;
            }
        });
    }

    function endSession(): void {
        error = null;
        if (!bridgeCommandsAvailable()) {
            return;
        }
        bridgeCommand(buildConversationCommand("end"), (resp: any) => {
            if (!resp?.ok) {
                error = resp?.error ?? "end session failed.";
                return;
            }
            lastWrap = resp.wrap ?? null;
            started = false;
        });
    }

    function planReply(): void {
        void (async () => {
            error = null;
            replyOptions = [];
            const intent = intentEn.trim();
            if (!intent) {
                return;
            }
            if (!bridgeCommandsAvailable()) {
                return;
            }
            if (inFlight) {
                error = "Busy.";
                return;
            }
            inFlight = true;
            try {
                const startResp: any = await bridgeCommandPromise(
                    buildConversationCommand("plan_reply_async", { intent_en: intent }),
                );
                if (!startResp?.ok) {
                    error = startResp?.error ?? "plan-reply failed.";
                    return;
                }
                const result = await waitForJob(startResp.job_id);
                if (!result?.ok) {
                    error = result?.error ?? "plan-reply failed.";
                    return;
                }
                replyOptions = result.plan?.options_ko ?? [];
            } finally {
                inFlight = false;
            }
        })();
    }

    function usePlannedReply(textKo: string): void {
        message = textKo;
    }

    let applySuggestionsResult: string | null = null;
    function applySuggestions(): void {
        error = null;
        applySuggestionsResult = null;
        bridgeCommand(
            buildConversationCommand("apply_suggestions", { deck: applyDeck }),
            (resp: any) => {
                if (!resp?.ok) {
                    error = resp?.error ?? "apply suggestions failed.";
                    return;
                }
                applySuggestionsResult = `created notes: ${(resp.created_note_ids ?? []).join(", ")}`;
            },
        );
    }

    function exportTelemetry(): void {
        error = null;
        telemetryJson = "";
        if (!bridgeCommandsAvailable()) {
            return;
        }
        bridgeCommand(
            buildConversationCommand("export_telemetry", { limit_sessions: 50 }),
            (resp: any) => {
                if (!resp?.ok) {
                    error = resp?.error ?? "export telemetry failed.";
                    return;
                }
                telemetryJson = resp.json ?? "";
            },
        );
    }
</script>

<div class="page">
    <h1>Conversation Practice</h1>
    {#if !bridgeCommandsAvailable()}
        <p>Not running inside Anki.</p>
    {/if}

    <div class="row">
        <label for="decks">Decks</label>
        <select id="decks" multiple bind:value={selectedDecks} size="6">
            {#each deckOptions as d}
                <option value={d}>{d}</option>
            {/each}
        </select>
        <label for="topic">Topic</label>
        <input id="topic" bind:value={topicId} />
        <button on:click={start} disabled={inFlight}>Start</button>
    </div>

    <div class="row">
        <button type="button" on:click={() => (showSettings = !showSettings)}>
            {showSettings ? "Hide" : "Show"} Settings
        </button>
    </div>
    {#if showSettings && settings}
        <div class="gloss">
            <div class="row">
                <label for="provider">Provider</label>
                <select id="provider" bind:value={settings.provider}>
                    <option value="local">local</option>
                    <option value="openai">openai</option>
                    <option value="fake">fake (no LLM)</option>
                </select>
                <label for="model">Model</label>
                <input id="model" bind:value={settings.model} />
            </div>
            <div class="row">
                <label>
                    <input type="checkbox" bind:checked={settings.safe_mode} />
                    safe_mode
                </label>
                <label for="redaction">Redaction</label>
                <select id="redaction" bind:value={settings.redaction}>
                    <option value="none">none</option>
                    <option value="minimal">minimal</option>
                    <option value="strict">strict</option>
                </select>
                <label for="max_rewrites">max_rewrites</label>
                <input
                    id="max_rewrites"
                    type="number"
                    bind:value={settings.max_rewrites}
                    min="0"
                    max="10"
                />
            </div>
            <div class="row">
                <label for="lexeme_field_index">lexeme_field_index</label>
                <input
                    id="lexeme_field_index"
                    type="number"
                    bind:value={settings.lexeme_field_index}
                    min="0"
                    max="50"
                />
                <label for="lexeme_field_names">lexeme_field_names</label>
                <input
                    id="lexeme_field_names"
                    bind:value={lexemeFieldNamesText}
                    placeholder="e.g. Front, Korean"
                />
                <label>
                    <input
                        type="checkbox"
                        bind:checked={noGlossField}
                        on:change={() => {
                            if (noGlossField) {
                                settings.gloss_field_index = null;
                            } else if (settings.gloss_field_index == null) {
                                settings.gloss_field_index = 1;
                            }
                        }}
                    />
                    no_gloss_field
                </label>
                <label for="gloss_field_index">gloss_field_index</label>
                <input
                    id="gloss_field_index"
                    type="number"
                    bind:value={settings.gloss_field_index}
                    min="0"
                    max="50"
                    disabled={noGlossField}
                />
                <label for="gloss_field_names">gloss_field_names</label>
                <input
                    id="gloss_field_names"
                    bind:value={glossFieldNamesText}
                    placeholder="e.g. Back, English"
                    disabled={noGlossField}
                />
                <label for="snapshot_max_items">snapshot_max_items</label>
                <input
                    id="snapshot_max_items"
                    type="number"
                    bind:value={settings.snapshot_max_items}
                    min="1"
                    max="50000"
                />
            </div>
            <div class="row">
                <button
                    type="button"
                    on:click={() => {
                        error = null;
                        const payload = {
                            ...settings,
                            max_rewrites: Number(settings.max_rewrites),
                            lexeme_field_index: Number(settings.lexeme_field_index),
                            lexeme_field_names: lexemeFieldNamesText
                                .split(",")
                                .map((s) => s.trim())
                                .filter(Boolean),
                            gloss_field_index:
                                settings.gloss_field_index == null
                                    ? null
                                    : Number(settings.gloss_field_index),
                            gloss_field_names: glossFieldNamesText
                                .split(",")
                                .map((s) => s.trim())
                                .filter(Boolean),
                            snapshot_max_items: Number(settings.snapshot_max_items),
                        };
                        bridgeCommand(
                            buildConversationCommand("set_settings", payload),
                            (resp: any) => {
                                if (!resp?.ok) {
                                    error = resp?.error ?? "failed to save settings";
                                }
                            },
                        );
                    }}
                >
                    Save Settings
                </button>
            </div>

            <div class="row">
                <button
                    type="button"
                    on:click={() => (showExportTelemetry = !showExportTelemetry)}
                >
                    {showExportTelemetry ? "Hide" : "Show"} Export telemetry
                </button>
                {#if showExportTelemetry}
                    <button type="button" on:click={exportTelemetry}>Export</button>
                {/if}
            </div>
            {#if showExportTelemetry && telemetryJson}
                <textarea rows="8" style="width: 100%;" readonly>
                    {telemetryJson}
                </textarea>
            {/if}
        </div>
    {/if}

    <div class="chat">
        {#each turns as turn, idx}
            <div class="msg user">{turn.user_text_ko}</div>

            <div class="msg assistant">
                <div class="assistantText">
                    <div>
                        {#each tokenizeForUi(turn.assistant.assistant_reply_ko) as tok}
                            {#if tok.kind === "word"}
                                <button
                                    type="button"
                                    class="tok"
                                    aria-label={`token ${tok.text}`}
                                    on:mouseenter={(e) =>
                                        showWordTooltip(
                                            tok.text,
                                            turn.assistant.word_glosses,
                                            e,
                                        )}
                                    on:mouseleave={hideTooltip}
                                >
                                    {tok.text}
                                </button>
                            {:else}
                                <span>{tok.text}</span>
                            {/if}
                        {/each}
                    </div>
                    <div>{turn.assistant.follow_up_question_ko}</div>
                </div>

                <div class="row">
                    <button
                        type="button"
                        on:click={() =>
                            (showHintByTurn = toggleByIndex(showHintByTurn, idx))}
                    >
                        Hint
                    </button>
                    <button
                        type="button"
                        on:click={() =>
                            (showExplainByTurn = toggleByIndex(showExplainByTurn, idx))}
                    >
                        Explain
                    </button>
                    <button
                        type="button"
                        on:click={() =>
                            toggleTranslate(idx, turn.assistant.assistant_reply_ko)}
                    >
                        Translate
                    </button>
                </div>

                {#if showHintByTurn[idx]}
                    <div class="gloss">
                        <div>
                            targets_used: {(turn.assistant.targets_used ?? []).join(
                                ", ",
                            )}
                        </div>
                        {#if (turn.assistant.unexpected_tokens ?? []).length}
                            <div>
                                unexpected_tokens: {(
                                    turn.assistant.unexpected_tokens ?? []
                                ).join(", ")}
                            </div>
                        {/if}
                    </div>
                {/if}

                {#if showExplainByTurn[idx]}
                    <div class="gloss">
                        {#if turn.assistant.micro_feedback}
                            <div>{turn.assistant.micro_feedback.content_ko}</div>
                            {#if turn.assistant.micro_feedback.content_en}
                                <div>{turn.assistant.micro_feedback.content_en}</div>
                            {/if}
                        {:else}
                            <div>(no feedback)</div>
                        {/if}
                        {#if turn.assistant.suggested_user_intent_en}
                            <div>
                                Suggested intent (EN):
                                {turn.assistant.suggested_user_intent_en}
                            </div>
                        {/if}
                    </div>
                {/if}

                {#if showTranslateByTurn[idx]}
                    <div class="gloss">
                        {#if translationByTurn[idx]}
                            <div>{translationByTurn[idx]}</div>
                        {:else}
                            <div>(loading…)</div>
                        {/if}
                    </div>
                {/if}
            </div>
        {/each}
    </div>

    <div class="row">
        <input
            placeholder="Type Korean…"
            bind:value={message}
            disabled={inFlight}
            on:keydown={(e) => e.key === "Enter" && send()}
        />
        <button on:click={send} disabled={inFlight}>Send</button>
        {#if inFlight}
            <span class="gloss">(waiting…)</span>
        {/if}
    </div>

    {#if error}
        <div class="error">{error}</div>
    {/if}
    {#if lastJobDebug}
        <div class="gloss">job: {lastJobDebug}</div>
    {/if}

    <div class="row">
        <button on:click={refreshWrap}>Refresh Wrap</button>
        <button type="button" on:click={endSession}>End session</button>
    </div>

    {#if lastWrap}
        <div class="gloss">
            <div>
                <strong>Strengths:</strong>
                {(lastWrap.strengths ?? []).join(", ")}
            </div>
            <div>
                <strong>Reinforce:</strong>
                {(lastWrap.reinforce ?? []).join(", ")}
            </div>
            {#if lastWrap.suggested_cards?.length}
                <div>
                    <strong>Suggested cards:</strong>
                    {lastWrap.suggested_cards.length}
                </div>
            {/if}
        </div>
    {/if}

    <div class="row">
        <button type="button" on:click={() => (showPlanReply = !showPlanReply)}>
            {showPlanReply ? "Hide" : "Show"} Plan my reply
        </button>
    </div>
    {#if showPlanReply}
        <div class="gloss">
            <div class="row">
                <label for="intent">English intent</label>
                <input
                    id="intent"
                    bind:value={intentEn}
                    placeholder="What do you want to say?"
                />
                <button on:click={planReply}>Generate</button>
            </div>
            {#if replyOptions.length}
                {#each replyOptions as opt}
                    <div class="row">
                        <button type="button" on:click={() => usePlannedReply(opt)}>
                            Use
                        </button>
                        <div>{opt}</div>
                    </div>
                {/each}
            {/if}
        </div>
    {/if}

    {#if lastWrap?.suggested_cards?.length}
        <div class="row">
            <button
                type="button"
                on:click={() => (showApplySuggestions = !showApplySuggestions)}
            >
                {showApplySuggestions ? "Hide" : "Show"} Add suggested cards to Anki
            </button>
        </div>
        {#if showApplySuggestions}
            <div class="gloss">
                {#each lastWrap.suggested_cards as sc}
                    <div>
                        <strong>{sc.front}</strong>
                        {#if sc.back}
                            — {sc.back}{/if}
                    </div>
                {/each}
                <div class="row">
                    <label for="applyDeck">Target deck</label>
                    <select id="applyDeck" bind:value={applyDeck}>
                        {#each deckOptions as d}
                            <option value={d}>{d}</option>
                        {/each}
                    </select>
                    <button on:click={applySuggestions}>Apply</button>
                </div>
                {#if applySuggestionsResult}
                    <div class="gloss">{applySuggestionsResult}</div>
                {/if}
            </div>
        {/if}
    {/if}

    {#if tooltip}
        <div class="tooltip" style="left: {tooltip.x}px; top: {tooltip.y}px;">
            <strong>{tooltip.text}</strong>
            : {tooltip.gloss}
        </div>
    {/if}
</div>

<style>
    .page {
        padding: 16px;
        font-family: system-ui, sans-serif;
        background: var(--canvas-inset, #fff);
        color: var(--fg, #111);
    }
    .row {
        display: flex;
        gap: 8px;
        align-items: center;
        margin: 8px 0;
    }
    .chat {
        border: 1px solid var(--border, #888);
        background: var(--canvas, #fff);
        padding: 8px;
        min-height: 200px;
        margin: 12px 0;
        border-radius: 8px;
    }
    .msg {
        margin: 6px 0;
        white-space: pre-wrap;
        color: var(--fg, #111);
    }
    .msg.user {
        text-align: right;
    }
    .tok {
        border: 0;
        background: transparent;
        padding: 0;
        margin: 0;
        font: inherit;
        cursor: pointer;
        color: var(--fg-link, var(--fg, #111));
        text-decoration: underline;
        text-underline-offset: 2px;
    }
    .gloss {
        margin-top: 8px;
        color: var(--fg-subtle, var(--fg, #111));
        font-size: 0.9em;
    }
    .error {
        margin-top: 8px;
        color: var(--accent-danger, #b00020);
    }
    .tooltip {
        position: absolute;
        background: var(--canvas, #fff);
        border: 1px solid var(--border, #888);
        padding: 4px 8px;
        border-radius: 4px;
        font-size: 0.85em;
        box-shadow: 0 2px 8px rgba(0, 0, 0, 0.15);
        z-index: 1000;
        max-width: 300px;
        pointer-events: none;
    }
</style>
