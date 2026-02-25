# Web Client

## What Is the Web Client?
Access your notes from any web browser.
- Run StillPoint as a web server
- Access from phones, tablets, or other computers
- Same features as the desktop app

## Starting the Web Server
Run StillPoint with web client enabled:
```bash
stillpoint --web --port 3000
```
- Opens a web interface on the specified port
- Access at `http://localhost:3000`

## Features in Web Client
- Full note editing with Markdown
- Task management and calendar
- Search across all notes
- AI chat (if configured)
- Responsive design for mobile devices

## Accessing from Other Devices
1. Start the web server on your main computer
2. Find your computer's IP address
3. On another device, open browser to: `http://IP_ADDRESS:3000`
4. Use the same interface as on desktop

## Security Considerations
- Web client runs on your local network by default
- For remote access, use VPN or secure tunnel
- No authentication by default - add `--auth` flag
- Consider firewall settings

## Mobile Access
- Optimized for touch devices
- Swipe gestures for navigation
- Keyboard shortcuts work on mobile browsers
- Syncs changes across devices

## Differences from Desktop
- Some file operations may be limited
- Drag-and-drop works in modern browsers
- Performance depends on network speed

## Tips
- Use for quick access from mobile devices
- Great for sharing notes with others locally
- Combine with remote vaults for full access anywhere
- Check browser compatibility for best experience
