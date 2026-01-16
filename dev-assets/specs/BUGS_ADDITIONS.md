
### Things to fix:
Running log, I'm sure there will be many!

#### UI Stuff
* Right click on a page, 'open in new window'.  This page should be editable but none of the navigation keys should work.  any links clicked on should open in the 'main window' not the opened orphan window.  The orphaned window is fully editable otherwise, and should still save/autosave like the others.
* Print to browser feature.
  * This feature should create a 'tmp' folder in the OS TMP dir
  * This will copy all assets from the page folder into this temp dir.
  * This will render the markdown page we are on as a PageName.html file.
    * All links in this HTML file will be relative to folder root so they are clickable in the browser
    * Meaning, images render in brower, any links to file attachments will open in the browser.
* Load Vaults feature
  * We should have a recently used set of opened vaults.
  * We should be able to select the `default` vault.
  * If more than one vault defined and default not selected we need a open vault dialogue on application startup.
* I want to support pasting in HTTP links.
  * If i paste a link into the buffer it should just render the link
    * e.g.   http://www.google.com?xyz
    * if I 'edit' the link and give it a friendly name is should support
    * i am adding [https://google.com|links] to other places.
      * This would display in the editor as a hyperlink of  just "Link Label"
      * Status bar would show the actual link.
      * Click or enter while mouse is over will activate the link using whatever the OS uses to handle the link.
      * Need to fix keyboad enter on [https://www.google.com|inline] links in bullet mode.
      * ctrl-k on a bullet line should still ~~strike~~ it.
      * multi select line, ctrl-k, should strike each line?
      * shift-: in vi mode should select to the end of the line.
* with multi lines selected
  * tab should move them all as a block 
  * shift-tab should unindent them a tab
  
 #### Bad Links
 [https://hibbett.atlassian.net/wiki/spaces/HS/pages/5674991664/2.2+-+USPS+Return+Label+TSD?focusedCommentId=5826347020|link in confluence]
 
			[https://hibbett.atlassian.net/wiki/spaces/HS/pages/5674991664/2.2+-+USPS+Return+Label+TSD?focusedCommentId=5826347020|]
			when you ctrl-e and edit the above.
	

#### Server Stuff
* Embedded Images
  * What really happens with files on the server?
    * We should probably post those files up to the API so they will be ready for a mobile app or a server decoupled from the client.
    * Expectations:
      * I paste a image into the window.
      * The file gets posted to the api, then api/server writes to disk
      * The file still needs to be loaded when page renders?
      * what are good options here?  keep a local on disk cache of the file for quick reading/display?
        * Or, should we always read/write through the API so its clean?

#### Startup Stuff
* there is still a noticeable lag on startup or certain page loads, even when froze and bundled.
  * gotta get that fixed.
  
#### Navigation stuff
* When you click on a bookmark button, make sure the editor is focused after page load.
* Any time you navigate away from a page, make sure the editor saves (hot keys, any clicks, etc) so we don't lose work. There may be instances when it does not happen.  can we do this smartly on lost of page focus or something?

#### Editor Stuff
* When you start a line with a dash...then hit enter it goes into bullet mode on the next line, that is wrong. it should just be a normal new line.
* if there are colons in page text with a space ': ' don't treat that as a link in the editor.
* on a line after inserting a link with ctrl-l, i should be able to keep typing without that adding to the link (so link should end after i hit spacebar).

#### Markdown support
* can we make the `markdown` blocks render in a fixed width font as well for visual treatment?


