<script setup lang="ts">
import { onLaunch } from "@dcloudio/uni-app";

import { setReauthenticator } from "@/services/api";
import { useAppStore } from "@/stores/app";
import { useAssetStore } from "@/stores/assets";
import { useAuthStore } from "@/stores/auth";
import { useDiagnosisStore } from "@/stores/diagnoses";
import { useDeletionStore } from "@/stores/deletion";
import { useJobStore } from "@/stores/jobs";
import { useOptimizationStore } from "@/stores/optimizations";
import { usePhotoDeletionStore } from "@/stores/photo-deletion";
import { useShareStore } from "@/stores/shares";

const appStore = useAppStore();
const assetStore = useAssetStore();
const authStore = useAuthStore();
const diagnosisStore = useDiagnosisStore();
const deletionStore = useDeletionStore();
const jobStore = useJobStore();
const optimizationStore = useOptimizationStore();
const photoDeletionStore = usePhotoDeletionStore();
const shareStore = useShareStore();

onLaunch(() => {
  // 服务端拒绝凭据时由 api 层回调重新登录。这里注入而非在 api.ts 中直接
  // import auth store，避免与 auth store 对 apiRequest 的依赖形成循环。
  setReauthenticator(() => authStore.renewAfterRejection());
  appStore.hydrate();
  assetStore.hydrate();
  authStore.hydrate();
  diagnosisStore.hydrate();
  deletionStore.hydrate();
  jobStore.hydrate();
  optimizationStore.hydrate();
  photoDeletionStore.hydrate();
  shareStore.hydrate();
  void authStore.authenticate().then(async () => {
    await deletionStore.reconcileIdentity(authStore.userId);
    if (deletionStore.active || deletionStore.inferredCompleted) {
      uni.reLaunch({ url: "/pages/profile/deletion" });
    }
  });
});
</script>

<style lang="scss">
@use "@/styles/tokens.scss" as *;

page {
  min-height: 100%;
  background: $color-paper;
  color: $color-ink;
  font-family:
    "PingFang SC",
    "Hiragino Sans GB",
    sans-serif;
}

view,
text,
button {
  box-sizing: border-box;
}

button::after {
  border: 0;
}
</style>
