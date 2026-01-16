Backlinks:
The database should store links between pages.
there is already a database table 'links' that track the from link and the to link path.

When pages are saved I want any links updated in this database.
When a page is deleted I want any links to that page to be deleted from the 'links' table.

--------------------

Visual Links Pane:
Next to the attachments tab on the right panel i want a tab called 'Link Navigator'.
This tab should show a visual indicator of the 'graph of pages' from the index with the current page as the center of the graph.  This should be a bubble type graph like Obsidian (looks like a molecule).
The current page should be a larger bubble in the center, leaf pages (links) should be depicted as smaller.
Each bubble should have its Page name on or under it whichever makes sense.
when i click a leaf node, it should load that page in the editor and also update the graph (so that the new age is now the center of the graph, etc).

I can right click in this graph to toggle the graph (default) or just the text of the raw data (link from and link to).

---------------------

In  the editor:
There should be a right click option in both the file navigator on the left as well as right clicking on any open space in the current editor to 'Backlinks...'.

Clicking this will activate the 'Link Navigator' tab for this page if it it not focused.

