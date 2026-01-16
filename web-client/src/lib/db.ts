import Dexie from 'dexie';
import type { Table } from 'dexie';

export interface Page {
  page_id: string;
  path: string;
  title: string;
  content?: string;
  updated: number;
  rev: number;
  deleted?: boolean;
  pinned?: boolean;
  parent_path?: string;
}

export interface CachedTree {
  id: number;
  path: string;
  tree: any;
  cached_at: number;
}

export interface Task {
  task_id: string;
  path: string;
  line: number;
  text: string;
  status: string;
  priority?: number;
  due?: string;
  starts?: string;
}

export interface OutboxItem {
  id?: number;
  page_id: string;
  path: string;
  content: string;
  rev: number;
  created_at: number;
  retry_count: number;
}

export class StillPointDatabase extends Dexie {
  pages!: Table<Page, string>;
  tree!: Table<CachedTree, number>;
  tasks!: Table<Task, string>;
  outbox!: Table<OutboxItem, number>;

  constructor() {
    super('stillpoint-web-client');
    
    this.version(1).stores({
      pages: 'page_id, path, updated, rev, deleted',
      tree: '++id, path, cached_at',
      tasks: 'task_id, path, status',
      outbox: '++id, page_id, created_at'
    });
  }
}

export const db = new StillPointDatabase();
