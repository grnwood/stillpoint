# AI insights for tasks

## Requirements
If 'ai chats' feature is enabled, i want to add ai task sumary and chat to my tasks.

The task panel will always have a 'last ai summary' data stored in the sqlite db for the app. The summary will be the result of they [task-tab-insight-prompt.txt](sp/app/task-tab-insight-prompt.txt)  prompt executed against the currently configured defualt server and model.  Results get stored/updated back into the sqlite db if task tab is later viewed again.

If this button is pressed for the first time and there are no stored insights they will be performed and results saved  in the database for next load.

The full context of the task window should be RAG'ed do the chroma database.  This means the count of tags, the task text, the priority, the due or start dates and the page path.  this should RAG'ed in chunks under the "StillPoint Tasks" key.


## UI Features
On the task panel add the [ai.svg](sp/assets/ai.svg) button next to my plus/minus resize text button.

When clicked this will open a panel that overtakes the current task list.  It will be split into two rows.  the top half will be the AI insights space which displays the last AI summary generation (or streams the results of the currently executing ai summary into). Style it similar to how the calendar page AI insights appears.  It will have a refresh button along side it which will cause the entire process to be redone (re RAG the task information, and rerun the prompt).

The bottom half will have a similar 'console like' chat (same as the ai chat panel).  use the same icons and features for this area.  This will allow the user to 'chat their tasks' having the RAG'ed task data in context.

