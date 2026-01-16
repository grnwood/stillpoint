import { db } from './db';
import { apiClient } from './api';

export class SyncManager {
  private syncInterval: number | null = null;
  private lastSyncRev: number = 0;
  private isSyncing: boolean = false;

  async startSync() {
    // Load last sync revision from localStorage
    const stored = localStorage.getItem('last_sync_rev');
    this.lastSyncRev = stored ? parseInt(stored, 10) : 0;

    // Initial sync
    await this.pullChanges();

    // Start periodic sync (every 30 seconds)
    this.syncInterval = window.setInterval(() => {
      this.pullChanges();
      this.pushChanges();
    }, 30000);
  }

  stopSync() {
    if (this.syncInterval) {
      clearInterval(this.syncInterval);
      this.syncInterval = null;
    }
  }

  async pullChanges() {
    if (this.isSyncing) return;
    
    try {
      this.isSyncing = true;
      const result = await apiClient.syncChanges(this.lastSyncRev);
      
      // Update local cache with changes
      for (const change of result.changes) {
        if (change.deleted) {
          await db.pages.delete(change.page_id);
        } else {
          // Fetch content if we don't have it cached
          const existing = await db.pages.get(change.page_id);
          if (!existing || existing.rev < change.rev) {
            try {
              const content = await apiClient.readPage(change.path);
              await db.pages.put({
                page_id: change.page_id,
                path: change.path,
                title: change.title,
                content: content.content,
                updated: change.updated,
                rev: change.rev,
                deleted: change.deleted,
                pinned: change.pinned,
              });
            } catch (error) {
              console.error(`Failed to fetch content for ${change.path}:`, error);
            }
          }
        }
      }

      // Update last sync revision
      this.lastSyncRev = result.sync_revision;
      localStorage.setItem('last_sync_rev', String(this.lastSyncRev));
      
    } catch (error) {
      console.error('Pull sync failed:', error);
    } finally {
      this.isSyncing = false;
    }
  }

  async pushChanges() {
    const outboxItems = await db.outbox.toArray();
    
    for (const item of outboxItems) {
      try {
        const result = await apiClient.writePage(item.path, item.content, item.rev);
        
        // Remove from outbox on success
        await db.outbox.delete(item.id!);
        
        // Update local page with new rev
        if (result.rev) {
          await db.pages.update(item.page_id, { rev: result.rev });
        }
      } catch (error: any) {
        // Handle conflict
        if (error.message.includes('409')) {
          console.warn('Conflict detected for', item.path);
          // Leave in outbox for manual resolution
          await db.outbox.update(item.id!, { retry_count: item.retry_count + 1 });
        } else {
          console.error('Push failed for', item.path, error);
          await db.outbox.update(item.id!, { retry_count: item.retry_count + 1 });
        }
        
        // Remove from outbox if retry count exceeded
        if (item.retry_count >= 5) {
          await db.outbox.delete(item.id!);
          console.error('Giving up on', item.path, 'after 5 retries');
        }
      }
    }
  }

  async queueEdit(page_id: string, path: string, content: string, rev: number) {
    await db.outbox.add({
      page_id,
      path,
      content,
      rev,
      created_at: Date.now(),
      retry_count: 0,
    });

    // Try to push immediately if online
    if (navigator.onLine) {
      await this.pushChanges();
    }
  }

  async getOutboxCount(): Promise<number> {
    return db.outbox.count();
  }
}

export const syncManager = new SyncManager();
