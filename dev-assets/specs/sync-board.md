sync board feature.


This is a server feature that is initiated from the UI.

It is NOT tied to a page.  The UI is just used to negotiate a valid 'token' on the server.  Similar to a transiet 'print token'.		

it should start from homebase menu...

Homebase / Open syncboard.

this will open a a link to the configured homebase vault to a route on the server that passes in the token and renders a web page.

this webpage will be used as just a buffer to be able to copy and paste data between systems that are nomrally isolated.

any client that has the link with the valid token can view the page.  the page should boostrap a webxocket type approach so it cn recieves pushes and pulls so if two browsers are open to the same page/token you can see the data being pushed or edited.  there should be a visual indicator when one page paste or chane somethingon the other page, this can be a transiet visual effect just so the viwer can see the push changed something.


the page does NOT need ot be permantntly persisted, this is a transient transfer mechanism thing where the user would be expected to copy the buffer and paste it elsewhere manually.

there should be a status bar showing status messages of what the websocket is doing or see's.... 'changed published.. vs changes received'.   if the token is no longer valid the page should indicae that this pageie exppired and no longer allow edits.

there should be a nice shortut button to copy and paste the entire buffer.

it should be able to received a 'pasted image' from a buffer and execute OCR on it to spit out the text.

once a pasted image is OCR'ed that binary image can be removed from the server.
