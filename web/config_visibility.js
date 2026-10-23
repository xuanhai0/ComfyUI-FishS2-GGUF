/** Hide widgets managed by server_config while retaining values and serialization. */
import { app } from "../../scripts/app.js";
const MANAGED = {
  FishS2Generate: ["server_url"],
  S2VoiceCreate: ["executable", "model", "tokenizer", "voice_dir", "dll_dir", "cuda_device", "gpu_layers"],
};
app.registerExtension({
  name: "FishS2.ConfigVisibility",
  async beforeRegisterNodeDef(nodeType, nodeData) {
    const fields = MANAGED[nodeData.name];
    if (!fields) return;
    const originalCreated = nodeType.prototype.onNodeCreated;
    nodeType.prototype.onNodeCreated = function (...args) {
      const result = originalCreated?.apply(this, args);
      const node = this;
      const refresh = () => {
        const connected = !!node.inputs?.some(i => i.name === "server_config" && i.link != null);
        for (const name of fields) {
          const w = node.widgets?.find(x => x.name === name);
          if (!w) continue;
          if (connected && !w._fishS2Hidden) {
            w._fishS2OriginalType = w.type;
            w._fishS2OriginalComputeSize = w.computeSize;
            w.type = "hidden";
            w.computeSize = () => [0, -4];
            w._fishS2Hidden = true;
          } else if (!connected && w._fishS2Hidden) {
            w.type = w._fishS2OriginalType;
            w.computeSize = w._fishS2OriginalComputeSize;
            w._fishS2Hidden = false;
          }
        }
        node.setSize([node.size[0], node.computeSize()[1]]);
        node.setDirtyCanvas(true, true);
      };
      const oldConnections = node.onConnectionsChange;
      node.onConnectionsChange = function (...a) {
        const r = oldConnections?.apply(this, a);
        refresh();
        return r;
      };
      const oldConfigure = node.onConfigure;
      node.onConfigure = function (...a) {
        const r = oldConfigure?.apply(this, a);
        refresh();
        return r;
      };
      return result;
    };
  },
});
