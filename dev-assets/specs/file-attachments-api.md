Currently my app works with file attachments, but it does it locally.

Principle:  Continue to keep file attachments locally in the place they live now.  BUT when a file is added call the server to register a copy of it in the server.  This should store the contents of the file (copy) on the server amd a index in the sqlite db. if the server is running on the client (localhost) it does not need to make a copy of the file.  When a new db is indexed it should register or delete files that do not match the database.  All api's should reference the relevant root path (not hard device paths).   This way if the server is run elsewhere form the client this still works.

I want the attach files to work through the fastapi server.

I need to add three apis.

/files/attach 

    attach a file or files to a page (in both the db and the servers file storage)

/files/

    list the attached files for this page.

/files/delete

    delete the requested file or files.

log this into [Attachments] log prefix when it's pulling or retrieving data or deleting (or any errors therein).
