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
    let selectedDecks: string[] = [];
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
    let resolvedGlossesByTurn: Record<number, Record<string, string>> = {};
    let debugVocabByTurn: Record<
        number,
        Record<string, { band?: string; r?: number | null; stage?: number | null }>
    > = {};
    let plannedTargetsByTurn: Record<
        number,
        Array<{ id: string; type: string; surface_forms: string[]; gloss?: string | null }>
    > = {};
    let showHintByTurn: Record<number, boolean> = {};
    let showTranslateByTurn: Record<number, boolean> = {};
    let translationByTurn: Record<number, string> = {};
    let error: string | null = null;
    let planReplyDraftKo = "";
    let replyOptions: string[] = [];
    let applyDeck = "";
    let showApplyReinforced = false;
    let applyReinforcedResult: string | null = null;
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
    let tooltip: {
        token: string;
        gloss: string;
        debug: string | null;
        x: number;
        y: number;
        anchorX: number;
        arrowX: number;
        placement: "top" | "bottom";
    } | null = null;
    let tooltipEl: HTMLDivElement | null = null;
    let activeTooltipToken: string | null = null;
    let hoveredWordsThisTurn: Set<string> = new Set();

    // Korean particles ("josa") that frequently attach to nouns, e.g. "날씨가".
    // Keep in sync with pylib/anki/conversation/validation.py:_JOSA_SUFFIXES.
    const JOSA_SUFFIXES = [
        "에서",
        "으로",
        "하고",
        "이",
        "가",
        "은",
        "는",
        "을",
        "를",
        "에",
        "로",
        "와",
        "과",
        "랑",
        "도",
        "만",
    ].sort((a, b) => b.length - a.length);

    const glossCache = new Map<string, string | null>();

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
            if (!deckOptions.length) {
                selectedDecks = [];
                applyDeck = "";
                return;
            }

            // If the current selection contains decks that no longer exist, drop them.
            selectedDecks = selectedDecks.filter((d) => deckOptions.includes(d));
            if (!selectedDecks.length) {
                selectedDecks = [deckOptions[0]];
            }

            // Default apply-deck to the first selected deck.
            if (!applyDeck || !deckOptions.includes(applyDeck)) {
                applyDeck = selectedDecks[0] ?? deckOptions[0];
            }
        });
    });

    function sendEvent(payload: Record<string, unknown>): void {
        if (!bridgeCommandsAvailable()) {
            return;
        }
        bridgeCommand(buildConversationCommand("event", payload));
    }

    function glossFromMap(
        token: string,
        glosses: Record<string, string> | undefined,
    ): string | null {
        if (!glosses) {
            return null;
        }
        const direct = glosses[token];
        if (direct) {
            return direct;
        }
        for (const suffix of JOSA_SUFFIXES) {
            if (token.endsWith(suffix) && token.length > suffix.length) {
                const stem = token.slice(0, -suffix.length);
                const stemGloss = glosses[stem];
                if (stemGloss) {
                    return stemGloss;
                }
            }
        }
        return null;
    }

    function stemByStrippingJosa(token: string): string | null {
        for (const suffix of JOSA_SUFFIXES) {
            if (token.endsWith(suffix) && token.length > suffix.length) {
                return token.slice(0, -suffix.length);
            }
        }
        return null;
    }

    async function lookupGlossFromBackend(token: string): Promise<string | null> {
        if (!bridgeCommandsAvailable()) {
            return null;
        }
        try {
            const resp: any = await bridgeCommandPromise(
                buildConversationCommand("gloss", { lexeme: token }),
            );
            if (resp?.found && typeof resp.gloss === "string" && resp.gloss.trim()) {
                return resp.gloss.trim();
            }
        } catch {
            // ignore lookup failures
        }
        return null;
    }

    async function lookupGlossCached(token: string): Promise<string | null> {
        const normalized = token.trim();
        if (!normalized || normalized.length > 50) {
            return null;
        }
        if (glossCache.has(normalized)) {
            return glossCache.get(normalized) ?? null;
        }
        const gloss = await lookupGlossFromBackend(normalized);
        glossCache.set(normalized, gloss);
        return gloss;
    }

    async function resolveGloss(
        token: string,
        glosses: Record<string, string> | undefined,
    ): Promise<string | null> {
        const fromPrompt = glossFromMap(token, glosses);
        if (fromPrompt) {
            return fromPrompt;
        }
        const stem = stemByStrippingJosa(token);
        if (stem) {
            const stemFromPrompt = glossFromMap(stem, glosses);
            if (stemFromPrompt) {
                return stemFromPrompt;
            }
        }
        const fromDb = await lookupGlossCached(token);
        if (fromDb) {
            return fromDb;
        }
        if (stem) {
            return await lookupGlossCached(stem);
        }
        return null;
    }

    async function mapWithConcurrency<T, U>(
        items: readonly T[],
        limit: number,
        fn: (item: T) => Promise<U>,
    ): Promise<U[]> {
        const out: U[] = new Array(items.length) as U[];
        let i = 0;
        const workers = Array.from({ length: Math.max(1, limit) }, async () => {
            while (i < items.length) {
                const idx = i++;
                out[idx] = await fn(items[idx]);
            }
        });
        await Promise.all(workers);
        return out;
    }

    async function prefetchTurnGlosses(turnIndex: number, turn: Turn): Promise<void> {
        const base: Record<string, string> = {
            ...(turn.assistant.word_glosses ?? {}),
        };

        const tokens = [
            ...tokenizeForUi(turn.assistant.assistant_reply_ko),
        ]
            .filter((t) => t.kind === "word")
            .map((t) => t.text);

        for (const token of tokens) {
            if (base[token]) {
                continue;
            }
            const stem = stemByStrippingJosa(token);
            if (stem && base[stem]) {
                base[token] = base[stem];
            }
        }

        const missing = tokens.filter((t) => !base[t]);
        if (missing.length) {
            const resolved = await mapWithConcurrency(missing, 4, async (token) => {
                const gloss = await resolveGloss(token, base);
                return { token, gloss };
            });
            for (const { token, gloss } of resolved) {
                if (gloss) {
                    base[token] = gloss;
                }
            }
        }

        resolvedGlossesByTurn = { ...resolvedGlossesByTurn, [turnIndex]: base };
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
                if (
                    selectedDecks.length &&
                    (!applyDeck || !selectedDecks.includes(applyDeck))
                ) {
                    applyDeck = selectedDecks[0];
                }
                turns = [];
                resolvedGlossesByTurn = {};
                debugVocabByTurn = {};
                plannedTargetsByTurn = {};
                showHintByTurn = {};
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
                const newIndex = turns.length;
                debugVocabByTurn = {
                    ...debugVocabByTurn,
                    [newIndex]: (result as any).debug_vocab ?? {},
                };
                plannedTargetsByTurn = {
                    ...plannedTargetsByTurn,
                    [newIndex]: (result as any).planned_targets ?? [],
                };
                turns = [
                    ...turns,
                    {
                        user_text_ko: text,
                        assistant: (result as any).response,
                    },
                ];
                void prefetchTurnGlosses(turns.length - 1, turns[turns.length - 1]);
            } finally {
                inFlight = false;
            }
        })();
    }

    function resolveDebugInfo(
        token: string,
        debug:
            | Record<
                  string,
                  { band?: string; r?: number | null; stage?: number | null }
              >
            | undefined,
    ): { band?: string; r?: number | null; stage?: number | null } | null {
        if (!debug) {
            return null;
        }
        if (debug[token]) {
            return debug[token];
        }
        const stem = stemByStrippingJosa(token);
        if (stem && debug[stem]) {
            return debug[stem];
        }
        return null;
    }

    function debugLineForTooltip(
        token: string,
        debug:
            | Record<
                  string,
                  { band?: string; r?: number | null; stage?: number | null }
              >
            | undefined,
    ): string | null {
        const info = resolveDebugInfo(token, debug);
        if (!info) {
            return null;
        }
        const band = info.band ?? "?";
        const r =
            typeof info.r === "number" && Number.isFinite(info.r)
                ? info.r.toFixed(3)
                : "?";
        const stage =
            typeof info.stage === "number" && Number.isFinite(info.stage)
                ? String(info.stage)
                : null;
        return stage ? `band=${band}  R=${r}  stage=${stage}` : `band=${band}  R=${r}`;
    }

    function showWordTooltip(
        word: string,
        glosses: Record<string, string> | undefined,
        debug:
            | Record<
                  string,
                  { band?: string; r?: number | null; stage?: number | null }
              >
            | undefined,
        el: HTMLElement,
    ): void {
        activeTooltipToken = word;
        const gloss = glossFromMap(word, glosses);
        const dbg = debugLineForTooltip(word, debug);
        if (gloss) {
            const rect = el.getBoundingClientRect();
            const anchorX = rect.left + rect.width / 2;
            const preferTop = rect.top >= 44;
            const placement: "top" | "bottom" = preferTop ? "top" : "bottom";
            const y = preferTop ? rect.top - 10 : rect.bottom + 10;

            tooltip = {
                token: word,
                gloss,
                debug: dbg,
                x: anchorX,
                y,
                anchorX,
                arrowX: 0,
                placement,
            };

            // Clamp to viewport and position the arrow precisely over the word.
            requestAnimationFrame(() => {
                if (!tooltip || !tooltipEl || tooltip.token !== word) {
                    return;
                }
                const box = tooltipEl.getBoundingClientRect();
                const pad = 8;
                let dx = 0;
                if (box.left < pad) {
                    dx = pad - box.left;
                } else if (box.right > window.innerWidth - pad) {
                    dx = window.innerWidth - pad - box.right;
                }
                const newLeft = box.left + dx;
                const arrowX = Math.max(
                    12,
                    Math.min(tooltip.anchorX - newLeft, box.width - 12),
                );
                tooltip = { ...tooltip, x: tooltip.x + dx, arrowX };
            });
        } else {
            // Show a bubble immediately (so hover always does something), then fill.
            const rect = el.getBoundingClientRect();
            const anchorX = rect.left + rect.width / 2;
            const preferTop = rect.top >= 44;
            const placement: "top" | "bottom" = preferTop ? "top" : "bottom";
            const y = preferTop ? rect.top - 10 : rect.bottom + 10;
            tooltip = {
                token: word,
                gloss: "…",
                debug: dbg,
                x: anchorX,
                y,
                anchorX,
                arrowX: 0,
                placement,
            };
            void (async () => {
                const resolved = await resolveGloss(word, glosses);
                if (!resolved) {
                    return;
                }
                if (!tooltip || activeTooltipToken !== word || tooltip.token !== word) {
                    return;
                }
                tooltip = { ...tooltip, gloss: resolved };
            })();
        }
        // Track that the user hovered over this word (indicates they may not know it)
        hoveredWordsThisTurn.add(word);
        sendEvent({ type: "dont_know", token: word });
    }

    function hideTooltip(): void {
        activeTooltipToken = null;
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
            resolvedGlossesByTurn = {};
            debugVocabByTurn = {};
            plannedTargetsByTurn = {};
            showHintByTurn = {};
            showTranslateByTurn = {};
            translationByTurn = {};
            tooltip = null;
        });
    }

    function planReply(): void {
        void (async () => {
            error = null;
            replyOptions = [];
            const draftKo = planReplyDraftKo.trim();
            if (!draftKo) {
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
                    buildConversationCommand("plan_reply_async", { draft_ko: draftKo }),
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

    function useSuggestedReply(turn: Turn): void {
        const suggested = turn.assistant.suggested_user_reply_ko;
        if (typeof suggested === "string" && suggested.trim()) {
            message = suggested;
        }
    }

    function applyReinforced(): void {
        error = null;
        applyReinforcedResult = null;
        bridgeCommand(
            buildConversationCommand("apply_reinforced", { deck: applyDeck }),
            (resp: any) => {
                if (!resp?.ok) {
                    error = resp?.error ?? "apply reinforced words failed.";
                    return;
                }
                applyReinforcedResult = `created notes: ${(resp.created_note_ids ?? []).join(", ")}`;
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
                <label>
                    <input type="checkbox" bind:checked={settings.allow_new_words} />
                    allow_new_words
                </label>
                <label for="max_new_words_per_session">max_new_words_per_session</label>
                <input
                    id="max_new_words_per_session"
                    type="number"
                    bind:value={settings.max_new_words_per_session}
                    min="0"
                    max="50"
                />
                <label for="force_new_word_every_n_turns">force_new_word_every_n_turns</label>
                <input
                    id="force_new_word_every_n_turns"
                    type="number"
                    bind:value={settings.force_new_word_every_n_turns}
                    min="1"
                    max="10"
                />
                <label>
                    <input
                        type="checkbox"
                        bind:checked={settings.treat_unseen_deck_words_as_support}
                    />
                    treat_unseen_deck_words_as_support
                </label>
            </div>
            <div class="row">
                <label for="lexical_similarity_max">lexical_similarity_max</label>
                <input
                    id="lexical_similarity_max"
                    type="number"
                    step="0.05"
                    min="0.1"
                    max="0.95"
                    bind:value={settings.lexical_similarity_max}
                />
                <label for="semantic_similarity_max">semantic_similarity_max</label>
                <input
                    id="semantic_similarity_max"
                    type="number"
                    step="0.05"
                    min="0.1"
                    max="0.95"
                    bind:value={settings.semantic_similarity_max}
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
                            allow_new_words: Boolean(settings.allow_new_words),
                            max_new_words_per_session: Number(
                                settings.max_new_words_per_session,
                            ),
                            force_new_word_every_n_turns: Number(
                                settings.force_new_word_every_n_turns,
                            ),
                            treat_unseen_deck_words_as_support: Boolean(
                                settings.treat_unseen_deck_words_as_support,
                            ),
                            lexical_similarity_max: Number(
                                settings.lexical_similarity_max,
                            ),
                            semantic_similarity_max: Number(
                                settings.semantic_similarity_max,
                            ),
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
                {#if turn.assistant.micro_feedback?.content_en}
                    <div class="gloss">{turn.assistant.micro_feedback.content_en}</div>
                {:else}
                    <div class="gloss">(no feedback)</div>
                {/if}

                <div class="assistantText">
                    <div class="assistantLine">
                        {#each tokenizeForUi(turn.assistant.assistant_reply_ko) as tok}
                            {#if tok.kind === "word"}
                                <button
                                    type="button"
                                    class="tok"
                                    aria-label={`token ${tok.text}`}
                                    on:mouseenter={(e) =>
                                        showWordTooltip(
                                            tok.text,
                                            resolvedGlossesByTurn[idx] ??
                                                turn.assistant.word_glosses,
                                            debugVocabByTurn[idx],
                                            e.currentTarget as HTMLElement,
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
                            toggleTranslate(idx, turn.assistant.assistant_reply_ko)}
                    >
                        Translate
                    </button>
                </div>

                {#if showHintByTurn[idx]}
                    <div class="gloss">
                        {#if (plannedTargetsByTurn[idx] ?? []).length}
                            <div>
                                planned_targets: {(plannedTargetsByTurn[idx] ?? [])
                                    .map(
                                        (t) =>
                                            `${t.id}={${(t.surface_forms ?? []).join(",")}}`,
                                    )
                                    .join(" | ")}
                            </div>
                        {/if}
                        <div>
                            targets_used: {(turn.assistant.targets_used ?? [])
                                .map((id) => {
                                    const planned = (plannedTargetsByTurn[idx] ?? []).find(
                                        (t) => t.id === id,
                                    );
                                    if (!planned) {
                                        return id;
                                    }
                                    return `${id}={${(planned.surface_forms ?? []).join(",")}}`;
                                })
                                .join(", ")}
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

                {#if turn.assistant.suggested_user_reply_en}
                    <div class="gloss">
                        <div class="row">
                            <button type="button" on:click={() => useSuggestedReply(turn)}>
                                Use suggested reply
                            </button>
                            <div>
                                Suggested reply (EN): {turn.assistant.suggested_user_reply_en}
                            </div>
                        </div>
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
            {#if lastWrap.reinforced_words?.length}
                <div>
                    <strong>Reinforced words:</strong>
                    {lastWrap.reinforced_words.length}
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
                <label for="draftKo">Your draft (Korean)</label>
                <input
                    id="draftKo"
                    bind:value={planReplyDraftKo}
                    placeholder="Type your draft Korean reply…"
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

    {#if lastWrap?.reinforced_words?.length}
        <div class="row">
            <button
                type="button"
                on:click={() => (showApplyReinforced = !showApplyReinforced)}
            >
                {showApplyReinforced ? "Hide" : "Show"} Add reinforced words to Anki
            </button>
        </div>
        {#if showApplyReinforced}
            <div class="gloss">
                {#each lastWrap.reinforced_words as sc}
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
                    <button on:click={applyReinforced}>Apply</button>
                </div>
                {#if applyReinforcedResult}
                    <div class="gloss">{applyReinforcedResult}</div>
                {/if}
            </div>
        {/if}
    {/if}

    {#if tooltip}
        <div
            bind:this={tooltipEl}
            class="wordTooltip {tooltip.placement}"
            style="left: {tooltip.x}px; top: {tooltip.y}px; --arrow-left: {tooltip.arrowX}px;"
        >
            <div class="tooltipGloss">{tooltip.gloss}</div>
            {#if tooltip.debug}
                <div class="tooltipDebug">{tooltip.debug}</div>
            {/if}
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
    .assistantLine {
        line-height: 1.7;
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
    .wordTooltip {
        position: fixed;
        z-index: 10000;
        max-width: min(260px, calc(100vw - 16px));
        padding: 4px 8px;
        border-radius: 10px;
        background: var(--canvas, #fff);
        color: var(--fg, #111);
        border: 1px solid var(--border, #888);
        box-shadow: 0 8px 20px rgba(0, 0, 0, 0.2);
        pointer-events: none;
        white-space: nowrap;
        font-size: 12px;
        opacity: 1;
        display: block;
    }
    .tooltipDebug {
        margin-top: 2px;
        font-size: 0.78em;
        opacity: 0.75;
        font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas,
            "Liberation Mono", "Courier New", monospace;
        white-space: nowrap;
    }
    .wordTooltip.top {
        transform: translate(-50%, -100%);
    }
    .wordTooltip.bottom {
        transform: translate(-50%, 0%);
    }
    .tooltipGloss {
        opacity: 0.95;
    }
    .wordTooltip::before,
    .wordTooltip::after {
        content: "";
        position: absolute;
        left: var(--arrow-left, 50%);
        transform: translateX(-50%);
        width: 0;
        height: 0;
        border: 7px solid transparent;
    }
    .wordTooltip::after {
        border-width: 6px;
    }
    .wordTooltip.top::before {
        bottom: -14px;
        border-top-color: var(--border, #888);
    }
    .wordTooltip.top::after {
        bottom: -12px;
        border-top-color: var(--canvas, #fff);
    }
    .wordTooltip.bottom::before {
        top: -14px;
        border-bottom-color: var(--border, #888);
    }
    .wordTooltip.bottom::after {
        top: -12px;
        border-bottom-color: var(--canvas, #fff);
    }
</style>
