# Edit
Created Monday 19 January 2026
---
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
- [x] FullTextSearch  [:StillPointDevelopment:App:StillPointDevelopment:App:FullTextSearch|:StillPointDevelopment:App:FullTextSearch]
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