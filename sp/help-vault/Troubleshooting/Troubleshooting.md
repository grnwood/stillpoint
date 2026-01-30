# Troubleshooting

## Common Issues

### Pages Not Appearing
- **Problem**: New pages don't show in navigation
- **Solution**: Go to Vault → Rebuild Vault Index
- **Prevention**: The index rebuilds automatically, but manual rebuild helps

### Search Not Working
- **Problem**: Can't find pages or content
- **Solution**: Tools → Rebuild Search Index
- **Note**: Large vaults may take several minutes to index

### Slow Performance
- **Problem**: App feels sluggish
- **Solutions**:
  - Close unused windows
  - Enable lazy loading in preferences
  - Reduce number of open tabs
  - Check for large attachments

### Links Not Working
- **Problem**: Clicking links doesn't navigate
- **Solution**: Use Vault → Rebuild Vault Index
- **Check**: Make sure page names match exactly

### Tasks Not Showing
- **Problem**: Task panel is empty
- **Solution**: Check that tasks use correct syntax: `- [ ] Task text`
- **Note**: Tasks must be in Markdown files in your vault

### Calendar Not Showing Dates
- **Problem**: Calendar panel is empty
- **Solution**: Ensure dates are in YYYY-MM-DD format
- **Example**: Use `2025-01-15` not `January 15, 2025`

### AI Features Not Working
- **Problem**: AI chat or actions fail
- **Solutions**:
  - Check API key in preferences
  - Verify internet connection
  - Confirm AI is enabled for the vault

### Remote Vault Issues
- **Problem**: Can't connect to remote vault
- **Solutions**:
  - Check server URL and credentials
  - Verify network connectivity
  - Check server logs for errors

## Resetting Everything
If problems persist:
1. Close the app
2. Delete the config file (location shown in preferences)
3. Restart the app (it will create default settings)

## Getting Help
- Check the [GitHub issues](https://github.com/your-repo/issues) for known problems
- Include your OS, app version, and error messages when reporting

## Tips
- Keep your vault folder accessible and not heavily nested
- Regular backups prevent data loss
- Test features on small vaults first
