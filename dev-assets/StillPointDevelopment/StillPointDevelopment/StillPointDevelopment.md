# StillPointDevelopment
Logbook of all the development efforts on zim. yes!
---
### App
- [ ] check the vault lock (at least win32) should be warning, i lost writes over this @app
- [x] Add menus for both Format and Go.  [:FormatAndGo|FormatAndGo]
- [ ] have ai write a stillpoint vault of for itself. @app
- [x] major core dumps still (after nav fixes?) with @app
  - [x] coredumps!!  @coredump
    - [x] When messing with the jump / link boxes @todo 
    - [x] When flipping in/out of audience and focus mode (post nav fixes?) @todo 
- [ ] plantuml - need to not auto render .. make that ctrl-enter when on diagram @diagram
- [ ] maybe check into mermaid or other popular ones. @diagam
- [ ] opening the remote vault twice doesn't show the read lock @app

### Mobile PWA
- [ ] work on the vite app, make the default view nicer. @pwa

### Calendar
---
- [ ] not sure i really like the calendar panel, maybe need to rethink this. @cal
- [x] I'm seeing some blue dates on a fresh calendar, i only have the 24th defined?  @cal  
- [x] additional task support [:TaskSuppot|TaskSuppot]
- [x] Add Calendar panel  @cal
	move the calendar to its own tab (first)
	put the Journal part of the file tree under it.
	move tasks to its own 
	Tasks can have just the full rail.
	remove 'show jornal' from left nav since it's under calendar.
- [x] on cal panel insights for day page show all the headings in a clickable outline @cal
- [x] keep the filter across days applied, add a 'clear filter' above insight panel @cal
	(this is great for clicking around on a filtered view or either review or context on next steps).
- [x] add a 'today' button on the calendar widget @cal
- [x] add a recept pages edited search on api, tie it into the calendar feature @cal

### Tasks
- [ ] indented task should show slightly indented in task views @task
---

### File Nav
---
- [x] need to implement a true 'delete' @file
- [x] need to implement a true 'rename' @file
- [x] need to implement a true drag drop/move @file
[:FileNavResearch|FileNavResearch]
- [x] add a collapse all to top of nav, also a right click expand and collapse feature @file
- [ ] When right click new page in filtered mode, create that page under the filtered root not the vault root @file
- [ ] move and delete seem a little slow, need to speed that up and add a mouse wait state @file

### Edit
---
- [x] i would like to multi select turn into bullets with (*) or dashes with (-). @edit
- [x] editing with dashes/tabs get out of line on enter,etc, then when reloads lines up okay? wierd, janky @edit 
   - [x] recreate: go insert mode on a line, start typing and the view tabs (not whats on disk on reload). basically view and disk should look the same always. basically view and disk should look the same always.
 	  - [x] also, type a task indicator on a already existing line, screws it up on enter and skips undo buffer.
- [ ] something wonkey with move file nav @edit
   - [ ] Moving 'works' then refreshes with item back in it's old home... caching of nav?
   - [ ] alt tabbing away in edit mode focuses viewport at bottom, janky.
- [ ] when i return from focus or aud mode, remember font size and reapply upon return @edit
- [ ] write click in popup editor should have all options available except 'Navigate' menu, should work against window not main @edit
- [x] when i save a file sometimes it brings the viewport down to last line, janky @edit
- [x] if i edit a checkbox line by going to beginning and hit enter it's janky @edit
  - [ ] seems good on nix ... maybe win to confirm?
- [ ] when i use ctrl-l to insert link from popup window it puts link in main, needs to go in popup @edit
- [x] toc widget is still showing hr's, just moving them to the end, i want them supressed @edit
	:tocWidget 
- [ ] toy with idea of // or ''  to kickoff link insertion or creation. @edit
- [x] FullTextSearch  [:FullTextSearch|:FullTextSearch]
- [x] right click menus still suck, fix. also right click is losing selected buffer @edit
	[:RightClickDesignRightClickDesign
- [x] there is something wrong with my ctrl-tab history switcher... needs to be MRU.. @edit @wt
	MONITOR: i think this is working fine actually (2025-12-19).
- [x] when i paste a link i copied from a header or toc bar and paste into page it doesnt create the link in link navigator @edit <2025-12-29
	Ai is really not handling this well, I'm going to have to debug / fix this one myself.
- [ ] i added redo along with undo.  there is still something funky here but its mostly all usable, so leaving alone for now.  @edit
- [x] there is a c++/dump issue sometimes messing around in the jump to or link view, need to investigate. @edit
- [x] tags need to be smarter about emails @edit
		Username: joe.greenwood@capgemini.com
- [x] maybe ctrl-enter to get out of vi mode?  moving off home keys to esc is disrupting to flow. @edit
- [x] slight oddity with the dirty/save buffer on open as window (win32 only?) since vi edit mode maybe? @edit
- [x] opened window (at least on calendar) not treated as a first class window in the os tab switcher maybe win32 only? @edit
- [x] when i alt-tab back to docs editor should always load (and scroll if need be) to last cursor position @edit
- [x] when you add a camel case link the page reloads to render and puts cursor at top needs to remove location and scroll there @edit
- [x] when I launch http links (at least win32) it's launching them twice! @edit
- [x] There is some kind of colon window firing on colon type, remove.  Remove colon links entirely, they are useless and problematic @edit
- [x] When a camelcase link rerenders the page, the images go missing.  Also it jerks to the top, it needs to reload (if thats even necessary) to the original cursor point. @edit
- [x] i think my alt-left and alt-right is losing cursor position (starting back at the top)... annoying!  @edit
- [x] when i use vim 'p' with a image in the buffer it does not paste. default all image pastes to the width of the markdown editor? @edit
- [x] when editing a header item its janky , firm this out so it doesn't lose formatting until done. @edit
- [x] add a right click copy as ... html ... convert the selected buffer to basic html @edit
- [x] BUG: insert link is sometimes writing it at top of file!  super annoying ! @edit
- [x] I still see sometimes my page doesn't save, there is some kind of navigation why to lose the buffer, keep an eye out.  @edit
	pretty sure this is VI navigation (soft keys) doing this, need to make sure it fires a write ALL the time. Gotta hitj ctrl-s for now.
	I found **ONE** : If you activate a link before page saved it loses buffer, need to fix!
- [x] should i use fast api locally if server and ui are in the same place?  @edit
- [x] when you click a link from a floating editor to a anchor, show the animation  @edit
- [x] found the vi ; key bug, when i'm in caps it works, when lower it doesn't (maybe win32 only)  @edit
- [x] figure out a way to not allow crazy links to get generated and pasted @edit				￼
- [x] ￼smarter, like if its in a the middle of the sentence
 		or 8 : 56pm
 		of  This : That  <-- not a link.		
- [x] add find and replace functionality @edit
- [x] give the toc widget more prominence when hovering over a heading @edit
- [x] strange issue when you edit near the bottom of a page, it doesn't scroll, very annoying.  fix.  @edit
- [x] link pattern needs to be smarter too @edit
	e.g. this is not a link Password: Py9D+ssJ
	e.g. also plus mixed in some other words
		 ` node, detach+attach sub-trees, remove sub-trees).`
- [x] tasks: if you filter nav by either page, nav bar, or boomarks: make sure external task window gets a signal @edit @task
- [x] seems i lost my ctrl-alt-f focus view shortcut after full text search @edit
- [x] consider: when i change windows (gets focus) auto turn off vi mode? @edit
		Reason: switching always ends up editing crap (and a undo needed) before you realize it.
		Would be better to just have this mindset so it's more like (crap need a key press to edit)
		rather that oh eff.. undo this.
- [x] if in filtered mode, make the focus color around everything red!  @edit (cool)
- [ ] move  text option... pop up should say "move to page..." not "jump to page.." @edit
- [ ] right click "open command prompt here"  nix ... bash... win ... cmd/powershell @edit
- [x] soften vi mode focus loss.  shouldn't happen with alt-tab.

### Git
---
- [ ] git support: simple 'version this vault' @edit
	maybe version this vault, or page... with a note.
	System pref:  git control for vault?  default no.
	Requirements: 
		git installed in OS and on path. (or warning)
		attempt a git init.  Setup a remote origin and auto push?  (auto push on commit?)
		
### Indexer and Link Nav
---
- [x] sometimes ctrl-l link adding is give bad link targets: @index
￼
- [ ] lets fix the font and make it solid white, maybe have it scale down with the view size @link
- [x] if a back / forward / nav path results in a 404, remove it from recent links and history buffer @link
- [x] remember prefered / last used view and persist it @link
- [x] something is still very wrong with camel links and colon link resoultion. @edit
- [ ] need a way to print my tasks (esp. the current filter... print to browser?) @task
- [x] double check the searching seems to not be filtering right (tags and tasks text?) @task
	e.g. if i have a task tagged with multiples... it doesn't seem to 'and' the filtering (select tag1, should filter by that, etc) @task
- [x] move my indicators to the right of the panel so the panel shrinks better @task
	Or, a better treatment?
- [x] hierarchial tasks should show a slight visual representation in task bar @task
- [x] support hiearchy assignment  @task
- [x] if all tasks on a tag are complete, tag comes out of tags filter (unless show completed is selected)  @task
- [x] if tree is filtered, filter the tasks panel based on tree and sub pages? @task
	(show red filtered message next to tags label)
- [x] need a way to AI chat with my tasks. @task
- [x] when flipping toggles on tasks we should update the number next to the tag so counts and data match up. @task
	
### Network Vault
---
- [x] network vault UI should still write the file. @network
- [x] network vault UI should still post the attachment up to server @network
- [x] network vault needs to source images from remote (in UI) @network
- [x] network vault needs to paste attachments to the server @network
- [ ] network vault needs to read audio from server for playback @network


### Page Templates
---
- [ ] Add some more?  @page
- [ ] concept of project template? @page
	thinking something like:  right click / new project / TSD template.  This might create the folder / page structure automatically.
	other ideas:
		meetings? [:SomeAdditionalideasSomeAdditionalideas
- [x] ctrl-n should default to the `New Page` template.  @page
- [x] need to fix template engine. add a open template folder (users .stillpoint folder overrides). @page
	probably add !qotd! in there too for fun, any others?
		
### Printing
---
- [ ] print to browser[:print_to_browser| feature?  @print  <2025-12-01

### Ai Stuff
---
- [x] selected text, then ctrl/shift/p to type a adhoc prompt isn't sending properly @ai
- [x] need to add chroma/rag into chat.  @ai
	* add the page text into chroma by default?
	* (#) hash tag loads list of file attachements from page?
	* (@) allows linking to any other page in the notebook
	  * How to handle subfolders, etc?
	* Delete has to remove from chroma or get to big.
	* Chroma per chat?  or global chroma file for the whole app?
- [ ] when i send in ai prompts from main editor it doesn't register in that as sent, lets fix @ai
- [x] funky page load when sending to ai chat @ai
- [x] the ai tab when it gets focus should never load anything into the editor unless 'load chat for' is enabled. @ai
- [x] my wipe confirm thing is wired to 'refresh models' not the refresh chat button :)  @aii
- [x] seems like the ai widget and prompt for calendar ai summary cant be found in pyinstalled bundle? @ai
- [x] i lost my right click ai actions in the ai chat window text, lets put them back. @ai
  - [x] one shot promptiong @ai
- [x] bundling of ai daily prompt (Failed to load AI summary prompt.), check the bundling @ai

## Plant UML
---
- [x] plant uml support in the editor @nice
- [ ] make the editor prettier, its rough to look at, code highlighting?  does it exist @puml
- [ ] 'describe the diagram you want to generate' change to 'how do you want to create or update the...?' @puml

### Nice to Haves
---
- [x] extend tagging to pages, not just tasks @nice
	this is probably a key feature for some, i don't use it much bit i might start.
  - tags that are not part of a task....
  - should have a 'tag' on global search
  - probably implement global search first then.  [:FullTextSearch:FullTextSearch

### SVG Vector Repo!
[https://www.svgrepo.com/svg/445655/collapse-vertical|