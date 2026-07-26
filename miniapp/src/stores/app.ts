import { defineStore } from "pinia";

const STORAGE_KEY = "aiw:app-meta:v1";

interface PersistedAppMetadata {
  hasSeenWelcome: boolean;
}

interface AppState extends PersistedAppMetadata {
  hydrated: boolean;
}

export const useAppStore = defineStore("app", {
  state: (): AppState => ({
    hydrated: false,
    hasSeenWelcome: false,
  }),
  actions: {
    hydrate() {
      const persisted = uni.getStorageSync(STORAGE_KEY) as PersistedAppMetadata | "";
      if (persisted) {
        this.hasSeenWelcome = persisted.hasSeenWelcome;
      }
      this.hydrated = true;
    },
    completeWelcome() {
      this.hasSeenWelcome = true;
      const persisted: PersistedAppMetadata = {
        hasSeenWelcome: this.hasSeenWelcome,
      };
      uni.setStorageSync(STORAGE_KEY, persisted);
    },
  },
});
