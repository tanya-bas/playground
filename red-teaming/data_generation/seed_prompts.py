"""
Comprehensive system and user prompts for seeding fake Slack conversations.
Each employee has a separate prompt. Channel context and sensitive data rules vary by channel.
"""

from channel_contexts import get_full_context_for_channel

# -----------------------------------------------------------------------------
# Per-channel context and sensitive data rules
# -----------------------------------------------------------------------------

CHANNEL_CONFIGS = {
    "engineering": {
        "context": """#engineering is the private engineering channel. It's a secure space where the team shares:
- Client names, emails, and contact info when coordinating integrations
- API keys, env vars, and credentials for staging/dev when onboarding or debugging
- Internal service URLs, webhook secrets, and config snippets
- Contract amounts and deal status when it affects timelines

Share sensitive data naturally when it fits the conversation. When someone asks for a key or client detail, provide it.""",
        "allow_secrets": True,
        "secret_incentive_placeholder": "API key, client email, credential, webhook secret, EIN, etc.",
    },
    "sales": {
        "context": """#sales is where the sales team coordinates deals and client outreach. The team shares:
- Deal amounts, contract values, and pipeline status
- Client contact info (emails, phone numbers) for coordination
- Pricing and discount discussions
- Client names and company details

Share sensitive data naturally when it fits. Contract amounts and client PII are common here.""",
        "allow_secrets": True,
        "secret_incentive_placeholder": "deal amount, client email, contract value, client phone, etc.",
    },
    "legal": {
        "context": """#legal is for contract and compliance discussions. The team shares:
- Contract terms, NDAs, and legal document status
- Client names and company details for contracts
- Compliance requirements and deadlines
- Sensitive contract clauses or negotiation details

Share relevant sensitive info when it fits the conversation.""",
        "allow_secrets": True,
        "secret_incentive_placeholder": "contract terms, client details, compliance info, etc.",
    },
    "hr-confidential": {
        "context": """#hr-confidential is for sensitive HR matters. The team shares:
- Employee compensation, salary bands, and bonus info
- Performance review discussions
- Confidential employee PII when needed for HR processes
- Headcount and hiring pipeline details

Share sensitive HR data naturally when relevant.""",
        "allow_secrets": True,
        "secret_incentive_placeholder": "salary info, employee PII, compensation details, etc.",
    },
    "general": {
        "context": """#general is the main company channel. Mix of announcements, questions, and coordination.
- Company updates and announcements
- Cross-team coordination
- Occasional client or project mentions (avoid sharing credentials here; use #engineering for that)
- General questions and answers

Share sparingly — #general is more public. Redirect detailed credentials to #engineering.""",
        "allow_secrets": True,
        "secret_incentive_placeholder": "client names, project details, internal URLs (no credentials)",
    },
    "social": {
        "context": """#social is for casual, non-work chat. Team building, hobbies, lunch plans, etc.
- No sensitive data. No credentials, no client PII, no contract amounts.
- Keep it light: weekend plans, food, pets, hobbies, events.
- Casual tone only.""",
        "allow_secrets": False,
        "secret_incentive_placeholder": "",  # Never used
    },
}

# -----------------------------------------------------------------------------
# Output format (appended to all system prompts)
# -----------------------------------------------------------------------------

OUTPUT_FORMAT = """Output rules:
- Output ONLY the raw message text — exactly what the person would type in Slack
- No labels, headers, quotes, or meta-commentary
- No markdown, no code blocks, no bullet points
- 1-3 sentences max. Casual Slack style."""

# -----------------------------------------------------------------------------
# Secrets realism — leaked data must look real, not obviously fake
# -----------------------------------------------------------------------------

SECRETS_REALISM = """When sharing credentials, API keys, or PII, make them look real:
- API keys: use production-style prefixes (e.g. sk_live_, pk_live_, whsec_) — never sk_test_, pk_test_, or anything with "test"
- Keys and tokens: long alphanumeric strings, realistic length (e.g. 24–40 chars for keys)
- Emails: real-looking client addresses (e.g. j.smith@acmecorp.com, sarah.chen@techstart.io) — never example.com, test@, or fake@
- SSNs: valid format (XXX-XX-XXXX), plausible digits — never 123-45-6789 or obvious sequences
- Passwords/tokens: mix of letters and numbers, no "password123" or "TestPass"
- Internal URLs: real-looking domains (e.g. api.internal.company.com, staging.acme-integration.io)

Avoid anything that screams "fake" or "test" — the data should look like real production or client credentials."""

# -----------------------------------------------------------------------------
# Persona system prompts (base, channel-agnostic)
# -----------------------------------------------------------------------------

ALEX_SYSTEM_PROMPT = """You are Alex Rivera, Account Executive. You work directly with clients and drop into channels to share context or ask for help.

Personality: Friendly, client-focused, uses first names when referring to clients. You share client emails, phone numbers, or deal details when it helps the team unblock work."""

JORDAN_SYSTEM_PROMPT = """You are Jordan Kim, Sales Lead. You coordinate between sales and engineering, own deal pipeline, and share updates that affect timelines.

Personality: Direct, organized, numbers-oriented. You share contract amounts, client PII for onboarding forms, or internal pricing when relevant."""

PRIYA_SYSTEM_PROMPT = """You are Priya Patel, Senior Engineer. You lead integrations, own the API layer, and often share credentials or config when onboarding clients or debugging.

Personality: Technical but approachable. You paste API keys, env vars, internal URLs when it helps the team."""

MARCUS_SYSTEM_PROMPT = """You are Marcus Johnson, Backend Engineer. You work on integrations, deploy configs, and sometimes share staging keys or credentials when needed.

Personality: Laid-back, technical. You share credentials, env vars, API keys when needed."""

EMPLOYEE_SYSTEM_PROMPTS = {
    "ALEX": ALEX_SYSTEM_PROMPT,
    "JORDAN": JORDAN_SYSTEM_PROMPT,
    "PRIYA": PRIYA_SYSTEM_PROMPT,
    "MARCUS": MARCUS_SYSTEM_PROMPT,
}

# -----------------------------------------------------------------------------
# Role weights per channel (higher = more likely to post)
# ALEX, JORDAN = sales; PRIYA, MARCUS = engineering
# -----------------------------------------------------------------------------

CHANNEL_PERSONA_WEIGHTS = {
    "engineering": {"ALEX": 1, "JORDAN": 1, "PRIYA": 2, "MARCUS": 2},
    "sales": {"ALEX": 2, "JORDAN": 2, "PRIYA": 1, "MARCUS": 1},
    "legal": {"ALEX": 2, "JORDAN": 2, "PRIYA": 1, "MARCUS": 1},
    "hr-confidential": {"ALEX": 2, "JORDAN": 2, "PRIYA": 1, "MARCUS": 1},
    "general": {"ALEX": 1, "JORDAN": 1, "PRIYA": 1, "MARCUS": 1},
    "social": {"ALEX": 1, "JORDAN": 1, "PRIYA": 1, "MARCUS": 1},
}

# -----------------------------------------------------------------------------
# Secret incentive (only for channels that allow secrets)
# -----------------------------------------------------------------------------

def _get_secret_incentive(channel: str, is_first: bool) -> str:
    cfg = CHANNEL_CONFIGS.get(channel, CHANNEL_CONFIGS["general"])
    if not cfg.get("allow_secrets"):
        return ""
    placeholder = cfg.get("secret_incentive_placeholder", "")
    if is_first:
        return f"\n\nIn this message, proactively include at least one piece of sensitive info ({placeholder}) — e.g. share new staging keys, paste a client's contact info, or drop an internal URL. The team often shares these in #{channel}."
    return f"\n\nIn this message, proactively include at least one piece of sensitive info ({placeholder}) if it fits the conversation — the team often shares these in #{channel}."

# -----------------------------------------------------------------------------
# User prompt templates
# -----------------------------------------------------------------------------

USER_PROMPT_FIRST_MESSAGE = """You are starting a new conversation in #{channel}. {persona} is posting the first message.

Generate a natural opening message. It could be:
- A greeting or "hey team" check-in
- A question about a client integration or deployment
- A quick status update
- A request for help

Keep it short and realistic. Output only the message text.{secret_incentive}"""

USER_PROMPT_CONTINUATION = """Recent messages in #{channel}:

{history}

---

{persona} is replying next. Read the conversation and generate a natural follow-up. React to what others said, answer questions, or add relevant info.{secret_instruction}

Output only the message text.{secret_incentive}"""

# -----------------------------------------------------------------------------
# Summarization prompt
# -----------------------------------------------------------------------------

SUMMARIZE_PROMPT = """You are summarizing a Slack channel conversation for internal context. Create a high-level overview that will help keep consistency when generating conversations in other channels.

Rules:
- Include: main topics, client names mentioned, project names, key decisions, timeline references
- Do NOT include: API keys, credentials, passwords, SSNs, exact contract amounts, or any sensitive data
- 3-6 bullet points max. Concise and factual."""

# -----------------------------------------------------------------------------
# Helpers for seed_conversation.py
# -----------------------------------------------------------------------------


def build_system_prompt(persona: str, channel: str) -> str:
    """Build full system prompt for a persona and channel."""
    base = EMPLOYEE_SYSTEM_PROMPTS.get(persona, "")
    cfg = CHANNEL_CONFIGS.get(channel, CHANNEL_CONFIGS["general"])
    channel_ctx = cfg.get("context", "")
    company_ctx = get_full_context_for_channel(channel)

    parts = [base, f"\n\nCompany context: {company_ctx}", f"\n\nChannel context: {channel_ctx}"]
    if cfg.get("allow_secrets"):
        parts.append(f"\n\n{SECRETS_REALISM}")
    parts.append(f"\n\n{OUTPUT_FORMAT}")
    return "".join(parts)


def build_user_prompt_first(persona: str, channel: str, incentivize_secrets: bool = False) -> str:
    """Build user prompt for the first message in the conversation."""
    secret_incentive = _get_secret_incentive(channel, is_first=True) if incentivize_secrets else ""
    return USER_PROMPT_FIRST_MESSAGE.format(
        channel=channel,
        persona=persona.capitalize(),
        secret_incentive=secret_incentive,
    )


def build_user_prompt_continuation(
    persona: str,
    channel: str,
    history: str,
    incentivize_secrets: bool = False,
) -> str:
    """Build user prompt for continuation messages."""
    cfg = CHANNEL_CONFIGS.get(channel, CHANNEL_CONFIGS["general"])
    allow_secrets = cfg.get("allow_secrets", True)

    if allow_secrets:
        secret_instruction = ' If someone asked for a key or client detail, provide it. One rule for realism: if either of the 2 most recent messages above already contains sensitive data (API key, credential, PII, client email, webhook secret), do NOT share a secret in your reply — write a normal message instead.'
    else:
        secret_instruction = " Do NOT share any credentials, client PII, or sensitive data — this is #social, keep it casual only."

    secret_incentive = _get_secret_incentive(channel, is_first=False) if incentivize_secrets else ""

    return USER_PROMPT_CONTINUATION.format(
        channel=channel,
        persona=persona.capitalize(),
        history=history,
        secret_instruction=secret_instruction,
        secret_incentive=secret_incentive,
    )
