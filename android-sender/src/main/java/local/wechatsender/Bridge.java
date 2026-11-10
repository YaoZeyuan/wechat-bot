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
            require(inChat(target), "会话校验失败，标题匹配数 " + titles(target).size()
                + "，可编辑控件数 " + matching(AccessibilityNodeInfo::isEditable).size()
                + "，页面控件：" + pageShape(target));
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
        List<AccessibilityNodeInfo> direct = conversationRows(target);
        require(direct.size() <= 1, "首页有多个同名会话");
        if (direct.size() == 1) {
            click(direct.get(0));
            pause();
            return;
        }
        List<AccessibilityNodeInfo> folded = matching(named("折叠的聊天"));
        if (folded.size() == 1) {
            click(folded.get(0));
            pause();
            List<AccessibilityNodeInfo> groups = conversationRows(target);
            require(groups.size() <= 1, "折叠列表有多个同名会话");
            if (groups.size() == 1) {
                click(groups.get(0));
                pause();
                return;
            }
            require(performGlobalAction(GLOBAL_ACTION_BACK), "无法退出折叠列表");
            pause();
            require(home(), "无法返回聊天首页");
        }
        click(unique(named("搜索"), "首页搜索入口"));
        pause();
        setText(unique(n -> n.isEditable(), "搜索输入框"), target);
        pause();
        List<AccessibilityNodeInfo> results = conversationRows(target);
        require(results.size() == 1, "目标会话匹配数为 " + results.size());
        click(results.get(0));
        pause();
    }

    private boolean home() {
        return !matching(named("通讯录")).isEmpty() && !matching(named("搜索")).isEmpty();
    }

    private List<AccessibilityNodeInfo> conversationRows(String target) {
        Set<AccessibilityNodeInfo> rows = new LinkedHashSet<>();
        for (AccessibilityNodeInfo node : matching(n -> !n.isEditable() && named(target).test(n))) {
            AccessibilityNodeInfo row = clickable(node);
            if (row != null && !row.isEditable() && !isWebSearch(row)) rows.add(row);
        }
        return new ArrayList<>(rows);
    }

    private boolean isWebSearch(AccessibilityNodeInfo row) {
        List<AccessibilityNodeInfo> descendants = new ArrayList<>();
        collect(row, descendants);
        for (AccessibilityNodeInfo node : descendants) {
            String label = value(node.getText()) + " " + value(node.getContentDescription());
            if (label.contains("搜一搜") || label.contains("网页搜索") || label.contains("搜索网络")) return true;
        }
        return false;
    }

    private List<AccessibilityNodeInfo> titles(String name) {
        int screenHeight = getResources().getDisplayMetrics().heightPixels;
        java.util.regex.Pattern groupTitle = java.util.regex.Pattern.compile(
            java.util.regex.Pattern.quote(name) + "\\s*[（(]\\d+[）)]");
        return matching(n -> {
            Rect bounds = new Rect();
            n.getBoundsInScreen(bounds);
            return !n.isEditable() && bounds.top < screenHeight * .15
                && (named(name).test(n) || groupTitle.matcher(value(n.getText())).matches()
                    || groupTitle.matcher(value(n.getContentDescription())).matches());
        });
    }

    private boolean inChat(String name) {
        return !titles(name).isEmpty() && matching(AccessibilityNodeInfo::isEditable).size() == 1;
    }

    private String pageShape(String target) {
        Set<String> markers = new LinkedHashSet<>();
        for (AccessibilityNodeInfo node : nodes()) {
            boolean nameMatch = value(node.getText()).toString().contains(target)
                || value(node.getContentDescription()).toString().contains(target);
            String type = value(node.getClassName()).toString();
            if (nameMatch || node.isEditable() || type.endsWith("EditText")) {
                Rect bounds = new Rect();
                node.getBoundsInScreen(bounds);
                markers.add((nameMatch ? "目标" : "输入") + "/" + type + "/"
                    + node.getViewIdResourceName() + "/y=" + bounds.top);
            }
        }
        return String.join(", ", markers);
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

    private static AccessibilityNodeInfo clickable(AccessibilityNodeInfo node) {
        while (node != null && !node.isClickable()) node = node.getParent();
        return node;
    }

    private static void click(AccessibilityNodeInfo node) throws IOException {
        node = clickable(node);
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
