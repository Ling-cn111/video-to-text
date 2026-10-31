package com.videototext.app;

import android.os.Bundle;

import com.getcapacitor.BridgeActivity;

public class MainActivity extends BridgeActivity {

    @Override
    public void onCreate(Bundle savedInstanceState) {
        // 必须在 super.onCreate 之前注册：BridgeActivity.onCreate 内部会调用 load()，
        // 而 load() 末尾会 onNewIntent(getIntent()) 把冷启动的分享意图派发给插件。
        // 晚于 super 注册的话，冷启动那一次分享就丢了（热启动不受影响，容易漏测）。
        registerPlugin(ShareIntentPlugin.class);
        super.onCreate(savedInstanceState);
    }
}
