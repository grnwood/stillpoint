from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional


@dataclass
class AIActionItem:
    title: str
    prompt: str
    special: Optional[str] = None


@dataclass
class AIActionGroup:
    title: str
    actions: List[AIActionItem]
    requires_code: bool = False


AI_ACTION_GROUPS: list[AIActionGroup] = [
    AIActionGroup(
        title="Chat",
        actions=[
            AIActionItem("Load Global Chat", "Switch AI chat to the global/root context."),
        ],
    ),
    AIActionGroup(
        title="Summarize",
        actions=[
            AIActionItem("Short Summary", "Summarize this note in 3–5 sentences."),
            AIActionItem("Bullet Summary", "Summarize into bullet points."),
            AIActionItem("Key Insights", "Extract key insights / themes."),
            AIActionItem("TL;DR", "Give a one-line TL;DR."),
            AIActionItem("Meeting Minutes Style", "Convert to meeting minutes."),
            AIActionItem("Executive Summary", "Summarize for an executive audience."),
        ],
    ),
    AIActionGroup(
        title="Rewrite / Improve Writing",
        actions=[
            AIActionItem("Rewrite for Clarity", "Rewrite for clarity."),
            AIActionItem("Rewrite Concisely", "Rewrite concisely."),
            AIActionItem("Rewrite More Detailed", "Rewrite with more detail."),
            AIActionItem("Rewrite More Casual", "Rewrite in a more casual tone."),
            AIActionItem("Rewrite More Professional", "Rewrite in a more professional tone."),
            AIActionItem("Rewrite as Email", "Rewrite this as an email."),
            AIActionItem("Rewrite as Action Plan", "Rewrite as an action plan."),
            AIActionItem("Rewrite as Journal Entry", "Rewrite as a journal entry."),
            AIActionItem("Rewrite as Tutorial / Guide", "Rewrite as a tutorial / guide."),
        ],
    ),
    AIActionGroup(
        title="Translate",
        actions=[
            AIActionItem("Auto-Detect → English", "Translate (auto-detect) to English."),
            AIActionItem("English → Spanish", "Translate from English to Spanish."),
            AIActionItem("English → French", "Translate from English to French."),
            AIActionItem("English → German", "Translate from English to German."),
            AIActionItem("English → Italian", "Translate from English to Italian."),
            AIActionItem("English → Chinese", "Translate from English to Chinese."),
            AIActionItem("English → Japanese", "Translate from English to Japanese."),
            AIActionItem("English → Korean", "Translate from English to Korean."),
            AIActionItem("Custom Language…", "", special="custom_translation"),
        ],
    ),
    AIActionGroup(
        title="Extract",
        actions=[
            AIActionItem("Tasks / To-Dos", "Extract tasks / to-dos."),
            AIActionItem("Dates / Deadlines", "Extract dates and deadlines."),
            AIActionItem("Names & People", "Extract names and people."),
            AIActionItem("Action Items", "Extract action items."),
            AIActionItem("Questions", "Extract questions."),
            AIActionItem("Entities & Keywords", "Extract entities and keywords."),
            AIActionItem("Topics / Tags", "Extract topics or tags."),
            AIActionItem("Structured JSON Data", "Extract structured JSON data."),
            AIActionItem("Links / URLs mentioned", "Extract links / URLs mentioned."),
        ],
    ),
    AIActionGroup(
        title="Analyze",
        actions=[
            AIActionItem("Sentiment Analysis", "Perform sentiment analysis."),
            AIActionItem("Tone Analysis", "Analyze tone."),
            AIActionItem("Bias / Assumptions", "Find bias or assumptions."),
            AIActionItem("Logical Fallacies", "Check for logical fallacies."),
            AIActionItem("Risk Assessment", "Provide a risk assessment."),
            AIActionItem("Pros & Cons", "List pros and cons."),
            AIActionItem("Root-Cause Analysis", "Perform root-cause analysis."),
            AIActionItem("SWOT Analysis", "Provide a SWOT analysis."),
            AIActionItem("Compare Against Another Note…", "", special="compare_note"),
        ],
    ),
    AIActionGroup(
        title="Explain",
        actions=[
            AIActionItem("Explain Like I'm 5", "Explain like I'm 5."),
            AIActionItem("Explain for a Beginner", "Explain for a beginner."),
            AIActionItem("Explain for an Expert", "Explain for an expert."),
            AIActionItem("Break Down Step-By-Step", "Break down step by step."),
            AIActionItem("Provide Examples", "Provide examples."),
            AIActionItem("Define All Concepts", "Define all concepts."),
            AIActionItem("Explain the Why Behind Each Step", "Explain the why behind each step."),
        ],
    ),
    AIActionGroup(
        title="Brainstorm",
        actions=[
            AIActionItem("Brainstorm Ideas", "Brainstorm ideas."),
            AIActionItem("Brainstorm Questions to Ask", "Brainstorm questions to ask."),
            AIActionItem("Alternative Approaches", "Suggest alternative approaches."),
            AIActionItem("Solutions to the Problem", "Suggest solutions to the problem."),
            AIActionItem("Potential Risks / Pitfalls", "List potential risks or pitfalls."),
            AIActionItem("Related Topics I Should Explore", "List related topics to explore."),
        ],
    ),
    AIActionGroup(
        title="Transform",
        actions=[
            AIActionItem("Convert to Markdown", "Convert to Markdown."),
            AIActionItem("Convert to Bullet Points", "Convert to bullet points."),
            AIActionItem("Convert to Outline", "Convert to an outline."),
            AIActionItem("Convert to Table", "Convert to a table."),
            AIActionItem("Convert to Checklist", "Convert to a checklist."),
            AIActionItem("Convert to JSON", "Convert to JSON."),
            AIActionItem("Convert to CSV", "Convert to CSV."),
            AIActionItem("Convert to Code Comments", "Convert to code comments."),
            AIActionItem("Convert to Script (Python / JS / Bash)", "Convert to a script (Python / JS / Bash)."),
            AIActionItem("Convert to Slide Outline", "Convert to a slide outline."),
        ],
    ),
    AIActionGroup(
        title="Code Actions",
        requires_code=True,
        actions=[
            AIActionItem("Explain Code", "Explain this code."),
            AIActionItem("Optimize Code", "Optimize this code."),
            AIActionItem("Refactor Code", "Refactor this code."),
            AIActionItem("Add Comments", "Add comments to this code."),
            AIActionItem("Convert to Another Language…", "Convert this code to another language."),
            AIActionItem("Generate Unit Tests", "Generate unit tests for this code."),
            AIActionItem("Find Bugs", "Find bugs in this code."),
        ],
    ),
    AIActionGroup(
        title="Research Helper",
        actions=[
            AIActionItem("Generate Questions I Should Ask", "Generate questions I should ask."),
            AIActionItem("List Assumptions", "List assumptions."),
            AIActionItem("Find Missing Info", "Find missing information."),
            AIActionItem("Provide Historical Context", "Provide historical context."),
            AIActionItem("Predict Outcomes", "Predict outcomes."),
            AIActionItem("Summarize Top Debates Around This Topic", "Summarize top debates around this topic."),
            AIActionItem("Give Related References / Sources (non-live)", "Give related references or sources (non-live)."),
        ],
    ),
    AIActionGroup(
        title="Creative",
        actions=[
            AIActionItem("Rewrite as Story", "Rewrite as a story."),
            AIActionItem("Rewrite as Poem", "Rewrite as a poem."),
            AIActionItem("Rewrite as Dialogue", "Rewrite as a dialogue."),
            AIActionItem("Rewrite as Song", "Rewrite as a song."),
            AIActionItem("Rewrite as Fiction Scene", "Rewrite as a fiction scene."),
            AIActionItem("Turn This Into: characters / plot / setting", "Turn this into characters, plot, and setting."),
        ],
    ),
    AIActionGroup(
        title="Chat-About-This Note",
        actions=[
            AIActionItem("Ask the AI About This Note", "Ask the AI about this note."),
            AIActionItem("Continue Thought from Here", "Continue the thought from here."),
            AIActionItem("What Should I Do Next Based on This Note?", "What should I do next based on this note?"),
            AIActionItem("How Can I Improve This?", "How can I improve this?"),
            AIActionItem("Generate Next Section", "Generate the next section."),
        ],
    ),
    AIActionGroup(
        title="Memory & Linking",
        actions=[
            AIActionItem("Suggest Tags", "Suggest tags."),
            AIActionItem("Suggest Backlinks", "Suggest backlinks."),
            AIActionItem("Build Concept Map", "Build a concept map."),
            AIActionItem("Identify Repeating Themes Across Notes", "Identify repeating themes across notes."),
        ],
    ),
    AIActionGroup(
        title="Privacy / Redaction",
        actions=[
            AIActionItem("Remove Personal Info", "Remove personal information."),
            AIActionItem("Anonymize Names", "Anonymize names."),
            AIActionItem("Anonymize Companies", "Anonymize companies."),
            AIActionItem("Detect Sensitive Content", "Detect sensitive content."),
        ],
    ),
    AIActionGroup(
        title="Debug Note Content",
        actions=[
            AIActionItem("Check for Contradictions", "Check for contradictions."),
            AIActionItem("Check for Missing Steps", "Check for missing steps."),
            AIActionItem("Check for Ambiguous Claims", "Check for ambiguous claims."),
            AIActionItem("Check for Outdated Info", "Check for outdated info."),
            AIActionItem("Validate Against External Knowledge (optional)", "Validate against external knowledge (optional)."),
        ],
    ),
]
