/** Fish S2 音色上传、试听、缓存列表和按模式切换控件。 */
import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";

app.registerExtension({
    name: "FishS2.AudioUpload",
    async beforeRegisterNodeDef(nodeType, nodeData) {
        // 生成节点连线变化时通知音色节点重新发现上游服务器配置。
        if (nodeData.name === "S2ServerControl") {
            const oldCreated = nodeType.prototype.onNodeCreated;
            nodeType.prototype.onNodeCreated = function (...args) {
                const result = oldCreated?.apply(this, args);
                const voiceDir = this.widgets?.find(w => w.name === "voice_dir");
                if (voiceDir) {
                    const oldCallback = voiceDir.callback;
                    voiceDir.callback = function (...a) {
                        const r = oldCallback?.apply(this, a);
                        setTimeout(() => app.graph?._nodes?.forEach(n => n._fishS2RefreshVoices?.()), 0);
                        return r;
                    };
                }
                return result;
            };
            return;
        }
        if (["FishS2Generate", "FishS2SingleGenerate"].includes(nodeData.name)) {
            const originalCreated = nodeType.prototype.onNodeCreated;
            nodeType.prototype.onNodeCreated = function (...args) {
                const result = originalCreated?.apply(this, args);
                const previous = this.onConnectionsChange;
                this.onConnectionsChange = function (...a) {
                    const r = previous?.apply(this, a);
                    // ComfyUI 的连接图可能在回调之后才更新，延迟一个事件循环读取。
                    setTimeout(() => app.graph?._nodes?.forEach(n => n._fishS2RefreshVoices?.()), 0);
                    return r;
                };
                return result;
            };
            return;
        }
        if (!["S2VoiceInput", "S2VoiceCreate"].includes(nodeData.name)) return;
        const old = nodeType.prototype.onNodeCreated;
        nodeType.prototype.onNodeCreated = function (...args) {
            const ret = old?.apply(this, args);
            const node = this;
            const find = name => node.widgets?.find(w => w.name === name);
            const filenameWidget = find("uploaded_audio");
            if (!filenameWidget) return ret;
            const isInput = nodeData.name === "S2VoiceInput";
            const player = document.createElement("audio");
            player.controls = true;
            player.preload = "metadata";
            player.style.width = "100%";
            player.style.height = "38px";
            const sourceUrl = name => api.apiURL(`/view?filename=${encodeURIComponent(name)}&type=input&subfolder=FishS2Input`);
            const refreshPlayer = () => { player.src = filenameWidget.value ? sourceUrl(filenameWidget.value) : ""; };
            let audioWidget;
            if (node.addDOMWidget) {
                audioWidget = node.addDOMWidget("试听上传音频", "audio", player, {
                    serialize: false, getValue: () => undefined, setValue: () => {},
                });
            }
            if (!isInput) node.addWidget("button", "↺ 从音频文件名重置 Voice Name", null, () => {
                const w = find("voice_name"); if (w && filenameWidget.value) {
                    w.value = String(filenameWidget.value).replace(/\.[^.]+$/, "").replace(/[^A-Za-z0-9_-]/g, "_");
                    w._fishS2Auto = true;
                }
            }, {serialize:false});
            const uploadWidget = node.addWidget("button", "📂 上传音频 WAV/MP3/FLAC/OGG/M4A", null, () => {
                const picker = document.createElement("input");
                picker.type = "file";
                picker.accept = ".wav,.mp3,.flac,.ogg,.m4a,audio/*";
                picker.onchange = async () => {
                    const file = picker.files?.[0];
                    if (!file) return;
                    const form = new FormData();
                    form.append("file", file);
                    try {
                        const res = await api.fetchApi("/fishs2/audio/upload", {method: "POST", body: form});
                        const data = await res.json();
                        if (!res.ok || !data.ok) throw new Error(data.error || `HTTP ${res.status}`);
                        filenameWidget.value = data.name;
                        if (!isInput) {
                            const name = find("voice_name");
                            if (name && (!name.value || name.value === "my_voice" || name._fishS2Auto)) {
                                name.value = file.name.replace(/\.[^.]+$/, "").replace(/[^A-Za-z0-9_-]/g, "_") || "my_voice";
                                name._fishS2Auto = true;
                            }
                        }
                        refreshPlayer();
                        node.setDirtyCanvas(true, true);
                    } catch (err) {
                        console.error("[FishS2 upload]", err);
                        window.alert(`Fish S2 音频上传失败：${err.message}`);
                    }
                };
                picker.click();
            }, {serialize: false});

            // 不移除 widget：保留原值、原有序列化顺序以及旧工作流兼容性。
            function visible(widget, show) {
                if (!widget) return;
                if (!show && !widget._fishS2Hidden) {
                    widget._fishS2Type = widget.type;
                    widget._fishS2ComputeSize = widget.computeSize;
                    widget.type = "hidden";
                    widget.computeSize = () => [0, -4];
                    widget._fishS2Hidden = true;
                    if (widget.element) widget.element.style.display = "none";
                } else if (show && widget._fishS2Hidden) {
                    widget.type = widget._fishS2Type;
                    widget.computeSize = widget._fishS2ComputeSize;
                    widget._fishS2Hidden = false;
                    if (widget.element) widget.element.style.display = "";
                }
            }
            const resize = () => {
                node.setSize([Math.max(node.size[0], 340), node.computeSize()[1]]);
                node.setDirtyCanvas(true, true);
            };
            let voiceSelect, refreshVoicesButton;
            let lastDirectory = null;
            let lastResolution = "";
            let requestSerial = 0;
            function sourceOfLink(linkId) {
                const link = linkId == null ? null : app.graph?.links?.[linkId];
                return link ? app.graph?.getNodeById(link.origin_id) : null;
            }
            function directoryFromServer(server) {
                if (!server || server.type !== "S2ServerControl") return "";
                return String(server.widgets?.find(w => w.name === "voice_dir")?.value || "").trim();
            }
            function resolveDirectory() {
                const direct = node.inputs?.find(i => i.name === "server_config")?.link;
                if (direct != null) {
                    const directory = directoryFromServer(sourceOfLink(direct));
                    return directory ? {directory, mode: "direct"} : {directory: "", mode: "invalid-direct"};
                }
                const dirs = new Set();
                let connectedServers = 0;
                for (const output of node.outputs || []) {
                    if (output.name !== "音色配置" && output.type !== "S2VOICE_CONFIG") continue;
                    for (const linkId of output.links || []) {
                        const link = app.graph?.links?.[linkId];
                        const target = link && app.graph?.getNodeById(link.target_id);
                        if (!target || !["FishS2Generate", "FishS2SingleGenerate"].includes(target.type)) continue;
                        const configInput = target.inputs?.find(i => i.name === "server_config");
                        if (configInput?.link == null) continue;
                        connectedServers++;
                        const dir = directoryFromServer(sourceOfLink(configInput.link));
                        if (dir) dirs.add(dir);
                    }
                }
                if (dirs.size > 1 || (connectedServers && dirs.size === 0)) {
                    return {directory: "", mode: dirs.size > 1 ? "conflict" : "invalid-auto"};
                }
                if (dirs.size === 1) return {directory: [...dirs][0], mode: "auto"};
                return {directory: "", mode: "manual"};
            }
            function isCached() { return find("source_mode")?.value === "缓存 .s2voice"; }
            function showSelectionStatus(message) {
                if (voiceSelect) {
                    voiceSelect.options.values = [message];
                    voiceSelect.value = message;
                }
            }
            async function loadVoices(force = false) {
                if (!isInput || !isCached()) return;
                const resolved = resolveDirectory();
                const signature = `${resolved.mode}:${resolved.directory}`;
                if (!force && signature === lastResolution) return;
                lastResolution = signature;
                lastDirectory = resolved.directory;
                const serial = ++requestSerial;
                if (!resolved.directory) {
                    showSelectionStatus(resolved.mode === "conflict"
                        ? "（多个服务器配置冲突，请直接连接 server_config）"
                        : resolved.mode === "invalid-direct" || resolved.mode === "invalid-auto"
                            ? "（服务器配置无有效 voice_dir）" : "（未发现服务器配置）");
                    resize();
                    return;
                }
                try {
                    const res = await api.fetchApi("/fishs2/voices/list", {
                        method: "POST", headers: {"Content-Type": "application/json"},
                        body: JSON.stringify({voice_dir: resolved.directory}),
                    });
                    const data = await res.json();
                    if (serial !== requestSerial) return;
                    if (!res.ok || !data.ok) throw new Error(data.error || `HTTP ${res.status}`);
                    const choices = data.voices.length ? data.voices : ["（目录中没有 .s2voice）"];
                    voiceSelect.options.values = choices;
                    const id = find("voice_id");
                    if (data.voices.includes(id.value)) voiceSelect.value = id.value;
                    else if (data.voices.length) { voiceSelect.value = data.voices[0]; id.value = data.voices[0]; }
                    else voiceSelect.value = choices[0];
                } catch (err) {
                    if (serial !== requestSerial) return;
                    showSelectionStatus("（读取失败，请刷新）");
                    console.warn("[FishS2 voices]", err);
                }
                resize();
            }
            function refreshMode() {
                if (!isInput) return;
                const cached = isCached();
                const resolved = resolveDirectory();
                const hasServer = resolved.mode !== "manual";
                const canSelect = Boolean(resolved.directory);
                visible(find("uploaded_audio"), !cached);
                visible(find("reference_text"), !cached);
                visible(audioWidget, !cached);
                visible(uploadWidget, !cached);
                // 未发现服务器时仍允许手工 ID；冲突时明确提示，避免静默选错音色。
                visible(find("voice_id"), cached && !canSelect);
                visible(voiceSelect, cached && hasServer);
                visible(refreshVoicesButton, cached && hasServer);
                if (audioWidget?.element) audioWidget.element.style.display = cached ? "none" : "";
                if (cached) loadVoices();
                resize();
            }
            if (isInput) node._fishS2RefreshVoices = () => {
                if (isCached()) refreshMode();
            };
            if (isInput) {
                voiceSelect = node.addWidget("combo", "已缓存音色", "（未发现服务器配置）", value => {
                    if (value && !value.startsWith("（")) find("voice_id").value = value;
                }, {values: ["（未发现服务器配置）"], serialize: false});
                refreshVoicesButton = node.addWidget("button", "↻ 刷新音色列表", null, () => loadVoices(true), {serialize: false});
                const mode = find("source_mode");
                const originalCallback = mode?.callback;
                if (mode) mode.callback = function (...a) {
                    originalCallback?.apply(this, a);
                    refreshMode();
                };
                const oldConnections = node.onConnectionsChange;
                node.onConnectionsChange = function (...a) {
                    const r = oldConnections?.apply(this, a);
                    lastResolution = "";
                    setTimeout(refreshMode, 0);
                    return r;
                };
            }
            const oldConfigured = node.onConfigure;
            node.onConfigure = function (...a) {
                const result = oldConfigured?.apply(this, a);
                refreshPlayer();
                if (isInput) { lastResolution = ""; setTimeout(refreshMode, 0); }
                return result;
            };
            refreshPlayer();
            if (isInput) refreshMode();
            else {
                const oldExecuted = node.onExecuted;
                node.onExecuted = function(message) {
                    oldExecuted?.apply(this, arguments);
                    const path = message?.fishs2_created_voice?.[0];
                    if (path) window.alert(`Fish S2 Voice Create · 保存成功：\n${path}`);
                };
                resize();
            }
            return ret;
        };
    },
});
