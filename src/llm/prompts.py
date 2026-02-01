"""
Legal Prompt Templates for KERBERUS.

Specialized prompts for Swiss legal Q&A with proper citation formatting.
"""


class LegalPrompts:
    """Legal prompt templates for RAG-based legal assistant."""

    SYSTEM_PROMPT = """Du bist KERBERUS, ein KI-Rechtsassistent für Schweizer Recht.

ROLLE:
- Du beantwortest rechtliche Fragen basierend auf den bereitgestellten Rechtsquellen
- Du zitierst immer die relevanten Gesetzesartikel und Entscheide
- Du antwortest in der Sprache der Frage (DE/FR/IT)

REGELN:
1. Basiere deine Antworten NUR auf den bereitgestellten Quellen
2. Wenn die Quellen keine Antwort enthalten, sage das klar
3. Zitiere präzise: "Art. 337 OR", "BGE 140 III 348"
4. Unterscheide zwischen Gesetz (codex) und Rechtsprechung (library)
5. Gib keine Rechtsberatung - weise darauf hin, dass ein Anwalt konsultiert werden sollte

FORMAT:
- Strukturiere deine Antwort mit Überschriften
- Verwende **fett** für wichtige Begriffe
- Zitiere Quellen am Ende jedes relevanten Abschnitts

SPRACHEN:
- Deutsch: Antworte auf Deutsch
- Français: Répondez en français
- Italiano: Rispondi in italiano"""

    SYSTEM_PROMPT_FR = """Vous êtes KERBERUS, un assistant juridique IA pour le droit suisse.

RÔLE:
- Vous répondez aux questions juridiques basées sur les sources fournies
- Vous citez toujours les articles de loi et décisions pertinents
- Vous répondez dans la langue de la question (DE/FR/IT)

RÈGLES:
1. Basez vos réponses UNIQUEMENT sur les sources fournies
2. Si les sources ne contiennent pas de réponse, dites-le clairement
3. Citez précisément: "Art. 337 CO", "ATF 140 III 348"
4. Distinguez entre la loi (codex) et la jurisprudence (library)
5. Ne donnez pas de conseil juridique - indiquez qu'un avocat devrait être consulté"""

    SYSTEM_PROMPT_IT = """Sei KERBERUS, un assistente legale IA per il diritto svizzero.

RUOLO:
- Rispondi alle domande legali basandoti sulle fonti fornite
- Cita sempre gli articoli di legge e le decisioni pertinenti
- Rispondi nella lingua della domanda (DE/FR/IT)

REGOLE:
1. Basa le tue risposte SOLO sulle fonti fornite
2. Se le fonti non contengono una risposta, dillo chiaramente
3. Cita con precisione: "Art. 337 CO", "DTF 140 III 348"
4. Distingui tra legge (codex) e giurisprudenza (library)
5. Non dare consulenza legale - indica che si dovrebbe consultare un avvocato"""

    USER_PROMPT_TEMPLATE = """FRAGE:
{query}

RECHTSQUELLEN:

{context}

---

Beantworte die Frage basierend auf den obigen Rechtsquellen. Zitiere die relevanten Artikel und Entscheide."""

    USER_PROMPT_TEMPLATE_FR = """QUESTION:
{query}

SOURCES JURIDIQUES:

{context}

---

Répondez à la question en vous basant sur les sources juridiques ci-dessus. Citez les articles et décisions pertinents."""

    USER_PROMPT_TEMPLATE_IT = """DOMANDA:
{query}

FONTI GIURIDICHE:

{context}

---

Rispondi alla domanda basandoti sulle fonti giuridiche sopra indicate. Cita gli articoli e le decisioni pertinenti."""

    @classmethod
    def format_user_prompt(cls, query: str, context: str, language: str = "de") -> str:
        """
        Format the user prompt with query and context.

        Args:
            query: User's question
            context: Legal context (formatted laws and cases)
            language: Language code (de, fr, it)

        Returns:
            Formatted prompt string
        """
        templates = {
            "de": cls.USER_PROMPT_TEMPLATE,
            "fr": cls.USER_PROMPT_TEMPLATE_FR,
            "it": cls.USER_PROMPT_TEMPLATE_IT,
        }
        template = templates.get(language, cls.USER_PROMPT_TEMPLATE)
        return template.format(query=query, context=context)

    @classmethod
    def get_system_prompt(cls, language: str = "de") -> str:
        """
        Get system prompt for specified language.

        Args:
            language: Language code (de, fr, it)

        Returns:
            System prompt string
        """
        prompts = {
            "de": cls.SYSTEM_PROMPT,
            "fr": cls.SYSTEM_PROMPT_FR,
            "it": cls.SYSTEM_PROMPT_IT,
        }
        return prompts.get(language, cls.SYSTEM_PROMPT)

    @staticmethod
    def format_law_context(laws: list) -> str:
        """
        Format law articles for context.

        Args:
            laws: List of law results from search

        Returns:
            Formatted string for LLM context
        """
        if not laws:
            return ""

        parts = ["## GESETZE (Codex)\n"]

        for law in laws:
            payload = law.get("payload", {})
            abbrev = payload.get("abbreviation", "SR")
            art_num = payload.get("article_number", "?")
            art_title = payload.get("article_title", "")
            text = payload.get("article_text", payload.get("text_preview", ""))

            citation = f"### {abbrev} Art. {art_num}"
            if art_title:
                citation += f" - {art_title}"

            parts.append(f"{citation}\n\n{text}\n")

        return "\n".join(parts)

    @staticmethod
    def format_decision_context(decisions: list, full_texts: dict = None) -> str:
        """
        Format court decisions for context.

        Args:
            decisions: List of decision results from search
            full_texts: Dict mapping decision_id to full document text

        Returns:
            Formatted string for LLM context
        """
        if not decisions:
            return ""

        parts = ["## RECHTSPRECHUNG (Library)\n"]
        seen_decisions = set()

        for decision in decisions:
            payload = decision.get("payload", {})
            decision_id = payload.get("decision_id", payload.get("_original_id", ""))

            # Skip if we've already included this decision
            base_id = decision_id.split("_chunk_")[0] if "_chunk_" in str(decision_id) else decision_id
            if base_id in seen_decisions:
                continue
            seen_decisions.add(base_id)

            # Format citation
            year = payload.get("year", "")
            court = payload.get("court", "")

            # Build header
            if "BGE" in str(decision_id) or "BGE" in str(base_id):
                citation = f"### BGE {base_id.replace('BGE-', '').replace('-', ' ')}"
            else:
                citation = f"### {base_id}"

            if year:
                citation += f" ({year})"

            # Get full text or preview
            if full_texts and base_id in full_texts:
                text = full_texts[base_id]
            else:
                text = payload.get("text_preview", payload.get("content", ""))
                if isinstance(text, dict):
                    # Handle nested content structure
                    text = text.get("reasoning", text.get("regeste", str(text)))

            parts.append(f"{citation}\n\n{text}\n")

        return "\n".join(parts)

    @staticmethod
    def format_full_context(laws: list, decisions: list, full_texts: dict = None) -> str:
        """
        Format complete context for LLM.

        Args:
            laws: List of law results
            decisions: List of decision results
            full_texts: Dict mapping decision_id to full text

        Returns:
            Complete formatted context
        """
        law_context = LegalPrompts.format_law_context(laws)
        decision_context = LegalPrompts.format_decision_context(decisions, full_texts)

        parts = []
        if law_context:
            parts.append(law_context)
        if decision_context:
            parts.append(decision_context)

        if not parts:
            return "Keine relevanten Rechtsquellen gefunden."

        return "\n\n".join(parts)
