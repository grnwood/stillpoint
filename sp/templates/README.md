# StillPoint Page Templates

Templates allow you to create consistent, structured pages with automatic variable substitution. StillPoint includes several built-in templates and supports custom templates that you can add yourself.

## How Templates Work

When you create a new page or journal entry, StillPoint applies a template that can include special variables enclosed in double curly braces `{{variable}}`. These variables are automatically replaced with real values based on the page being created.

## Installing Custom Templates

### User Templates Directory

To add your own templates, copy `.txt` files to:

**Windows:**
```
C:\Users\YourUsername\.stillpoint\templates\
```

**macOS/Linux:**
```
~/.stillpoint/templates/
```

User templates take precedence over built-in templates with the same name.

You can copy these templates into your home folders and edit them and/or create new ones.

### Creating a Template

1. Create a new `.txt` file in your templates directory (e.g., `MyTemplate.txt`)
2. Add your content with any variables you want (see list below)
3. Save the file
4. The template will appear in the journal template dropdown in Preferences

## Available Variables

### Date/Time Variables

- `{{YYYY}}` - Four-digit year (e.g., `2026`)
- `{{Month}}` - Full month name (e.g., `January`)
- `{{MM}}` - Two-digit month (e.g., `01`)
- `{{dd}}` - Two-digit day of month (e.g., `14`)
- `{{DOW}}` - Day of week (e.g., `Monday`)
- `{{DayDateYear}}` - Full formatted date (e.g., `Tuesday 14 January 2026`)

### Page Variables

- `{{PageName}}` - The name of the page being created
- `{{PageSlug}}` - The page name with spaces replaced by underscores
- `{{FolderName}}` - The name of the folder (only in folder templates)
- `{{FolderSlug}}` - The folder name with spaces replaced by underscores (folder templates only)
- `{{FolderPathSlug}}` - Full folder path in colon form, with spaces replaced by underscores
- `{{VaultName}}` - The active vault name
- `{{VaultSlug}}` - The vault name with spaces replaced by underscores
- `{{QOTD}}` - Quote of the day (fetched from online source if available)
- `{{cursor}}` - Cursor position marker (positions the cursor at this location after page creation)

## Built-in Page Templates

### Default.txt
Used for new regular pages (Ctrl+N)
```
# {{PageName}}
Created {{DayDateYear}}
---
```

### JournalDay.txt
Basic daily journal template
```
# {{DOW}}, {{Month}} {{dd}} {{YYYY}}
---
```

### FunJournalDay.txt
Enhanced daily journal with quote and structure
```
# {{DOW}}, {{Month}} {{dd}} {{YYYY}}
---
{{QOTD}}
---
{{cursor}}
## Today's Plan
---

## Today's Actual
---
```

### JournalMonth.txt
Monthly journal template
```
# Month {{Month}}
---
```

### JournalYear.txt
Yearly journal template
```
# Year {{YYYY}}
---
```

### MeetingNotes.txt
Structured meeting documentation
```
# {{PageName}}
**Date:** {{DayDateYear}}

{{cursor}}

## Attendees
- 

## Agenda
1. 

## Discussion Notes


## Decisions Made
- 

## Action Items
- [ ] 

## Next Meeting
**Date:** 
**Topics:** 
```

### ProjectPlan.txt
Complete project planning framework
```
# {{PageName}}
Created {{DayDateYear}}
---

## Overview
{{cursor}}

## Goals
- 

## Timeline
**Start Date:** 
**Target Completion:** 

## Milestones
- [ ] 

## Resources Needed
- 

## Risks & Mitigation
- 

## Status Updates
### {{DayDateYear}}

```

### WeeklyReview.txt
Weekly reflection and planning
```
# Weekly Review - {{PageName}}
Week of {{DayDateYear}}
---

{{cursor}}

## Wins This Week
- 

## Challenges
- 

## What I Learned
- 

## Tasks Completed
- [ ] 

## Tasks Carried Over
- [ ] 

## Focus for Next Week
1. 
2. 
3. 

## Notes

```

### BookNotes.txt
Reading notes and reflections
```
# {{PageName}}
Read {{DayDateYear}}
---

**Author:** 
**Genre:** 
**Status:** Reading / Completed

{{cursor}}

## Summary


## Key Takeaways
- 

## Favorite Quotes
> 

## My Thoughts


## Related Reading
- 

## Rating
/5
```

### ResearchNotes.txt
Academic or professional research documentation
```
# {{PageName}}
{{DayDateYear}}
---

**Source:** 
**Type:** Article / Book / Video / Other
**Date:** 
**URL:** 

{{cursor}}

## Summary


## Key Points
- 

## Quotes & References
> 

## My Analysis


## Related Topics
- 

## Follow-up Questions
- 

## Tags
@research
```

### DecisionLog.txt
Document important decisions and their rationale
```
# {{PageName}}
**Date:** {{DayDateYear}}
---

## Decision
{{cursor}}

## Context / Background


## Options Considered
1. 
2. 
3. 

## Chosen Option


## Reasoning


## Expected Outcomes
- 

## Review Date


## Actual Outcomes (Update Later)

```

### GoalPlanning.txt
Goal setting and tracking
```
# {{PageName}}
Created {{DayDateYear}}
---

## Goal Statement
{{cursor}}

## Why This Matters


## Success Criteria
- 

## Action Steps
- [ ] 
- [ ] 
- [ ] 

## Timeline
**Start:** 
**Target:** 
**Review Dates:** 

## Potential Obstacles
- 

## Support & Resources
- 

## Progress Log
### {{DayDateYear}}

```

### PersonContact.txt
Contact information and interaction history
```
# {{PageName}}
---

**Email:** 
**Phone:** 
**Organization:** 
**Role:** 

{{cursor}}

## Background


## Interaction History
### {{DayDateYear}}


## Topics of Interest
- 

## Follow-ups
- [ ] 

## Notes

```

### BrainstormIdea.txt
Quick idea capture
```
# {{PageName}}
{{DayDateYear}}
---

## The Idea
{{cursor}}

## Initial Thoughts


## Potential Applications
- 

## Questions to Explore
- 

## Related Ideas
- 

## Next Steps
- [ ] 

## Tags
@idea
```

### Retrospective.txt
Team or personal retrospective
```
# {{PageName}}
{{DayDateYear}}
---

## Period
**From:** 
**To:** 

{{cursor}}

## What Went Well ✅
- 

## What Could Be Improved 🔄
- 

## What We Learned 💡
- 

## Action Items
- [ ] 

## Appreciation & Shoutouts 🎉


## Next Retrospective
**Date:** 
```

### ClassNotes.txt
Academic class or lecture notes
```
# {{PageName}}
{{DayDateYear}}
---

**Instructor:** 
**Topic:** 

{{cursor}}

## Key Concepts
- 

## Notes


## Important Definitions


## Examples


## Questions
- 

## Homework / Action Items
- [ ] 

## Related Material
- 
```

### RecipeNote.txt
Recipe documentation and ratings
```
# {{PageName}}
---

**Source:** 
**Prep Time:** 
**Cook Time:** 
**Servings:** 

{{cursor}}

## Ingredients
- 

## Instructions
1. 

## Notes & Modifications


## Rating
/5

## Tags
@recipe
```

## Using Templates

### Page Templates

**Create a single page:**
1. Press Ctrl+N or right-click in the tree → "New Page"
2. Choose a template from the dropdown
3. Enter page name and click Create

### Folder Templates

**Create a multi-page structure:**
1. Right-click on a folder in the tree → "New from Folder Template…"
2. Browse the category tree (Technical, Research, Project-Management, Creative)
3. Select a folder template (e.g., TechnicalSpec, Novel-Writing)
4. View the preview of pages that will be created
5. Enter your folder name (e.g., "MyFeature", "Book1")
6. Click Create

All pages will be created in a new folder with your chosen name, with variables like `{{FolderName}}` automatically replaced.

### Set Default Journal Template

1. Open **File → Preferences** (or Ctrl+,)
2. Navigate to the **Templates** section
3. Select your preferred template from the "Journal Day Template" dropdown
4. Click **Save**

### Templates are Applied

- **Journal entries**: When you open "Today's Journal" or navigate to a date in the calendar
- **New pages**: When you press Ctrl+N (uses `Default.txt`)
- **Folder templates**: When you use "New from Folder Template..." context menu
- **Journal hierarchy**: Year, month, and day templates are applied automatically when creating journal structure

## Built-in Folder Templates

StillPoint includes professionally designed folder templates for common use cases:

**Technical/**
- **TechnicalSpec** - Complete technical specification (Overview, Architecture, API Design, Data Models, Testing Strategy)
- Coming soon: API-Documentation, System-Design

**Project-Management/**
- **Project-Kickoff** - Project planning structure (Charter, Stakeholders, Requirements, Timeline, Risks)
- Coming soon: Sprint-Planning, Product-Launch

**Research/**
- **Research-Paper** - Academic paper structure (Abstract, Introduction, Literature Review, Methodology, Results, Discussion, References)

**Creative/**
- **Novel-Writing** - Fiction writing framework (Characters, Plot Outline, World Building, Research Notes, Writing Progress)

## Creating Custom Folder Templates

1. Create a category folder: `~/.stillpoint/templates/folders/YourCategory/`
2. Create a template folder: `~/.stillpoint/templates/folders/YourCategory/YourTemplate/`
3. Add `.txt` files for each page you want in the structure
4. Use `{{FolderName}}` for the project name and `{{PageName}}` for individual pages
5. Use colon links to cross-reference: `[:{{FolderPathSlug}}:OtherPage|OtherPage]`

Example structure:
```
~/.stillpoint/templates/folders/
  MyCategory/
    MyTemplate/
      Overview.txt
      Details.txt
      Summary.txt
```

## Example Custom Template

Here's an example template for a meeting notes page:

**MeetingNotes.txt:**
```
# {{PageName}}
Date: {{DayDateYear}}
---

{{cursor}}

## Attendees
- 

## Agenda
- 

## Discussion
- 

## Action Items
- [ ] 

## Next Meeting
- 
```

## Tips

- Keep templates simple and focused on structure rather than content
- Use `{{cursor}}` to position the cursor where you'll start typing
- Use `{{QOTD}}` sparingly—it requires an internet connection
- Template names (file stems) are displayed in the UI, so use clear, descriptive names
- Test your templates by creating a new page—variables are replaced immediately

## Troubleshooting

**My template isn't showing up:**
- Ensure the file has a `.txt` extension
- Check that it's in the correct `.stillpoint/templates/` directory
- Restart StillPoint if you just added the template

**Variables aren't being replaced:**
- Make sure variables are wrapped in double curly braces: `{{Variable}}`
- Variable names are case-sensitive
- Check for typos in variable names

**Template content appears as-is:**
- Verify the template file encoding is UTF-8
- Ensure there are no special characters corrupting the file
