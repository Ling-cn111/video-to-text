package com.videototext.app;

import android.content.Intent;

import com.getcapacitor.JSObject;
import com.getcapacitor.Plugin;
import com.getcapacitor.PluginMethod;
import com.getcapacitor.PluginCall;
import com.getcapacitor.annotation.CapacitorPlugin;

/**
 * 接收「从别的 App 分享过来」的文本（ACTION_SEND）。
 *
 * ## 为什么必须自己写
 *
 * Capacitor 的 {@code @capacitor/app} 只处理 ACTION_VIEW：
 * {@code AppPlugin.handleOnNewIntent} 第一行就是
 * {@code if (!Intent.ACTION_VIEW.equals(action) || url == null) return;}，
 * 而 B站 的「分享」发的是 ACTION_SEND + EXTRA_TEXT，不带 data URI，
 * 所以那条通道完全收不到。整个 node_modules 里 grep ACTION_SEND 零命中。
 *
 * ## 冷启动与热启动
 *
 * 冷启动：{@code BridgeActivity.load()} 末尾会调 {@code onNewIntent(getIntent())}，
 * 此时插件已注册，事件正常触发。
 * 热启动（App 在后台，用户从 B站 分享）：{@code MainActivity.onNewIntent} →
 * {@code Bridge.onNewIntent} → 每个插件的 {@code handleOnNewIntent}。
 *
 * 两种情况下 JS 侧都可能还没挂上监听器（WebView 刚起、React 还没水合），
 * 所以 {@code notifyListeners} 的 retainUntilConsumed 传 true —— 参数会被暂存，
 * 等 JS 首次 addListener 时补发（见 {@code Plugin.sendRetainedArgumentsForEvent}）。
 * 这也意味着**不需要**再额外提供 getLaunchIntent 之类的主动拉取接口：
 * 主动拉取会和补发叠在一起，变成同一次分享提交两次任务。
 *
 * ## 为什么只把原始文本递过去
 *
 * 分享文案是整段文本（标题 + 链接 + 「复制打开」），URL 抽取放在 JS 侧的
 * {@code extractFirstUrl} 里做 —— 那里已有实现且被单测覆盖。
 * 原生侧只负责原样递过去，不做任何猜测。
 */
@CapacitorPlugin(name = "ShareIntent")
public class ShareIntentPlugin extends Plugin {

    static final String EVENT_SHARE = "share";

    @Override
    protected void handleOnNewIntent(Intent intent) {
        super.handleOnNewIntent(intent);
        if (intent == null || !Intent.ACTION_SEND.equals(intent.getAction())) return;

        String text = intent.getStringExtra(Intent.EXTRA_TEXT);
        if (text == null || text.trim().isEmpty()) return;

        JSObject payload = new JSObject();
        payload.put("text", text);
        String subject = intent.getStringExtra(Intent.EXTRA_SUBJECT);
        if (subject != null && !subject.trim().isEmpty()) {
            payload.put("subject", subject);
        }
        notifyListeners(EVENT_SHARE, payload, true);
    }

    /**
     * 让 JS 能判断原生侧是否真的装上了分享入口。
     * 用于 E2E / verify-shell 的自检，不做业务逻辑。
     */
    @PluginMethod
    public void isAvailable(PluginCall call) {
        JSObject ret = new JSObject();
        ret.put("available", true);
        call.resolve(ret);
    }
}
