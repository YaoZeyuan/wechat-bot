package local.wechatsender;

import android.accessibilityservice.AccessibilityService;
import android.graphics.Rect;
import android.net.LocalServerSocket;
import android.net.LocalSocket;
import android.os.Bundle;
import android.view.accessibility.AccessibilityEvent;
import android.view.accessibility.AccessibilityNodeInfo;
import java.io.*;
import java.nio.charset.StandardCharsets;
import java.util.*;
import java.util.function.Predicate;
import org.json.JSONObject;

public final class Bridge extends AccessibilityService {
    private static final String WECHAT = "com.tencent.mm";
    private LocalServerSocket listener;

    @Override protected void onServiceConnected() {
        try {
            listener = new LocalServerSocket("wechat_sender");
            LocalServerSocket socket = listener;
            new Thread(() -> serve(socket), "sender-usb").start();
        } catch (IOException error) {
            android.util.Log.e("WeChatSender", "无法启动 USB 服务", error);
        }
    }

    private void serve(LocalServerSocket socket) {
        while (true) {
            LocalSocket client;
            try { client = socket.accept(); }
            catch (IOException closed) { return; }
            try (LocalSocket peer = client) {
                if (peer.getPeerCredentials().getUid() != 2000) continue;
                peer.setSoTimeout(5000);
                BufferedReader reader = new BufferedReader(new InputStreamReader(peer.getInputStream(), StandardCharsets.UTF_8));
                PrintWriter writer = new PrintWriter(new OutputStreamWriter(peer.getOutputStream(), StandardCharsets.UTF_8), true);
                boolean[] clicked = {false};
                try {
                    String line = reader.readLine();
                    if (line == null) continue;
                    JSONObject request = new JSONObject(line);
                    JSONObject response = execute(request, clicked);
                    writer.println(response.put("ok", true));
                } catch (Exception error) {
                    writer.println(new JSONObject().put("ok", false).put("uncertain", clicked[0])
                        .put("error", String.valueOf(error.getMessage())));
                }
            } catch (Exception error) {
                android.util.Log.w("WeChatSender", "USB 请求结束", error);
            }
        }
    }

    private JSONObject execute(JSONObject request, boolean[] clicked) throws Exception {
        String action = request.getString("action");
        if (action.equals("status")) {
            return new JSONObject().put("nodes", nodes().size());
        }
        String target = request.getString("target");
        if (action.equals("prepare")) {
            if (!inChat(target)) locate(target);
            require(inChat(target), "会话标题未匹配");
            return new JSONObject();
        }
        require(action.equals("text"), "未知操作");
        require(inChat(target), "当前会话标题未匹配");
        AccessibilityNodeInfo input = unique(n -> n.isEditable(), "聊天输入框");
        require(input.getText() == null || input.getText().length() == 0 || input.isShowingHintText(),
            "输入框已有草稿");
        String text = request.getString("text");
        boolean dryRun = request.getBoolean("dry_run");
        int before = matching(n -> !n.isEditable() && text.contentEquals(value(n.getText()))).size();
        setText(input, text);
        if (dryRun) {
            setText(unique(n -> n.isEditable(), "聊天输入框"), "");
        } else {
            require(inChat(target), "发送前会话标题未匹配");
            AccessibilityNodeInfo send = unique(named("发送"), "发送按钮");
            clicked[0] = true;
            click(send);
            pause();
            require(inChat(target), "发送后会话标题未匹配");
            require(matching(n -> !n.isEditable() && text.contentEquals(value(n.getText()))).size() > before,
                "已点击发送，但未核对到完整文本");
        }
        return new JSONObject();
    }

    private void locate(String target) throws Exception {
        for (int attempt = 0; attempt < 5 && !home(); attempt++) {
            require(!nodes().isEmpty(), "无障碍未读到前台微信控件");
            require(performGlobalAction(GLOBAL_ACTION_BACK), "无法返回聊天首页");
            pause();
        }
        require(home(), "无法确认聊天首页");
        click(unique(named("搜索"), "首页搜索入口"));
        pause();
        setText(unique(n -> n.isEditable(), "搜索输入框"), target);
        pause();
        List<AccessibilityNodeInfo> results = matching(n -> !n.isEditable() && named(target).test(n));
        if (results.isEmpty()) {
            for (int attempt = 0; attempt < 3 && !home(); attempt++) {
                require(!nodes().isEmpty(), "微信控件已不可读");
                require(performGlobalAction(GLOBAL_ACTION_BACK), "无法返回聊天首页");
                pause();
            }
            require(home(), "无法返回聊天首页");
            click(unique(named("折叠的聊天"), "折叠的聊天入口"));
            pause();
            results = matching(named(target));
        }
        require(results.size() == 1, "目标会话没有唯一匹配");
        click(results.get(0));
        pause();
    }

    private boolean home() {
        return !matching(named("通讯录")).isEmpty() && !matching(named("搜索")).isEmpty();
    }

    private boolean inChat(String name) {
        android.util.DisplayMetrics display = getResources().getDisplayMetrics();
        return !matching(n -> {
            Rect bounds = new Rect();
            n.getBoundsInScreen(bounds);
            return !n.isEditable() && named(name).test(n) && bounds.top < display.heightPixels * .15;
        }).isEmpty() && matching(n -> n.isEditable()).size() == 1;
    }

    private List<AccessibilityNodeInfo> nodes() {
        AccessibilityNodeInfo root = getRootInActiveWindow();
        List<AccessibilityNodeInfo> result = new ArrayList<>();
        if (root != null && WECHAT.contentEquals(value(root.getPackageName()))) collect(root, result);
        return result;
    }

    private void collect(AccessibilityNodeInfo node, List<AccessibilityNodeInfo> result) {
        Rect bounds = new Rect();
        node.getBoundsInScreen(bounds);
        if (node.isVisibleToUser() && !bounds.isEmpty()) result.add(node);
        for (int i = 0; i < node.getChildCount(); i++) {
            AccessibilityNodeInfo child = node.getChild(i);
            if (child != null) collect(child, result);
        }
    }

    private List<AccessibilityNodeInfo> matching(Predicate<AccessibilityNodeInfo> predicate) {
        List<AccessibilityNodeInfo> result = new ArrayList<>();
        for (AccessibilityNodeInfo node : nodes()) if (predicate.test(node)) result.add(node);
        return result;
    }

    private AccessibilityNodeInfo unique(Predicate<AccessibilityNodeInfo> predicate, String label) throws IOException {
        List<AccessibilityNodeInfo> result = matching(predicate);
        require(result.size() == 1, label + "匹配数为 " + result.size());
        return result.get(0);
    }

    private static Predicate<AccessibilityNodeInfo> named(String label) {
        return n -> label.contentEquals(value(n.getText())) || label.contentEquals(value(n.getContentDescription()));
    }

    private static CharSequence value(CharSequence value) { return value == null ? "" : value; }

    private static void click(AccessibilityNodeInfo node) throws IOException {
        while (node != null && !node.isClickable()) node = node.getParent();
        require(node != null && node.performAction(AccessibilityNodeInfo.ACTION_CLICK), "控件无法点击");
    }

    private static void setText(AccessibilityNodeInfo node, String text) throws Exception {
        Bundle args = new Bundle();
        args.putCharSequence(AccessibilityNodeInfo.ACTION_ARGUMENT_SET_TEXT_CHARSEQUENCE, text);
        require(node.performAction(AccessibilityNodeInfo.ACTION_SET_TEXT, args), "无法写入文本");
        pause();
        require(node.refresh() && (text.contentEquals(value(node.getText())) ||
            (text.isEmpty() && node.isShowingHintText())), "输入框文本未通过校验");
    }

    private static void require(boolean condition, String message) throws IOException {
        if (!condition) throw new IOException(message);
    }

    private static void pause() throws InterruptedException { Thread.sleep(650); }
    @Override public void onAccessibilityEvent(AccessibilityEvent event) {}
    @Override public void onInterrupt() {}
    @Override public void onDestroy() {
        if (listener != null) try { listener.close(); } catch (IOException ignored) {}
        super.onDestroy();
    }
}
