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
    private static LocalServerSocket currentListener;
    private static volatile Bridge activeBridge;
    private String preparedTarget;

    @Override protected void onServiceConnected() {
        android.accessibilityservice.AccessibilityServiceInfo info = getServiceInfo();
        info.flags |= android.accessibilityservice.AccessibilityServiceInfo.FLAG_RETRIEVE_INTERACTIVE_WINDOWS;
        setServiceInfo(info);
        synchronized (Bridge.class) {
            activeBridge = this;
            if (currentListener != null) {
                android.util.Log.i("WeChatSender", "无障碍服务已重连，继续使用现有 USB 监听");
                return;
            }
            try {
                LocalServerSocket socket = new LocalServerSocket("wechat_sender");
                currentListener = socket;
                new Thread(() -> serve(socket), "sender-usb").start();
                android.util.Log.i("WeChatSender", "USB 监听已启动");
            } catch (IOException error) {
                android.util.Log.e("WeChatSender", "无法启动 USB 服务", error);
            }
        }
    }

    private static void serve(LocalServerSocket socket) {
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
                    Bridge bridge = activeBridge;
                    if (bridge == null) throw new IOException("手机无障碍服务尚未连接");
                    JSONObject response = bridge.execute(request, clicked);
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
            AccessibilityNodeInfo root = getRootInActiveWindow();
            Rect bounds = new Rect();
            if (root != null) root.getBoundsInScreen(bounds);
            JSONObject status = new JSONObject().put("nodes", nodes().size())
                .put("root_package", root == null ? JSONObject.NULL : value(root.getPackageName()).toString())
                .put("root_children", root == null ? 0 : root.getChildCount())
                .put("root_bounds", bounds.toShortString())
                .put("interactive_windows", getWindows().size());
            android.util.Log.i("WeChatSender", "控件诊断 " + status);
            return status;
        }
        if (action.equals("share_status")) {
            return new JSONObject().put("ready", !matching(named("最近聊天")).isEmpty())
                .put("sent_confirmation", sentConfirmation() != null);
        }
        String target = request.getString("target");
        if (action.equals("share")) return shareAsset(request, target, clicked);
        if (action.equals("prepare")) {
            preparedTarget = null;
            if (!inChat(target)) locate(target);
            preparedTarget = target;
            require(chatReady(target), "会话校验失败，标题匹配数 " + titles(target).size()
                + "，可编辑控件数 " + matching(AccessibilityNodeInfo::isEditable).size()
                + "，页面控件：" + pageShape(target));
            return new JSONObject();
        }
        require(action.equals("text"), "未知操作");
        require(chatReady(target), "当前会话标题未匹配");
        AccessibilityNodeInfo input = unique(n -> n.isEditable(), "聊天输入框");
        clearDraft(input);
        String text = request.getString("text");
        boolean dryRun = request.getBoolean("dry_run");
        int before = matching(n -> !n.isEditable() && text.contentEquals(value(n.getText()))).size();
        setText(input, text);
        if (dryRun) {
            setText(unique(n -> n.isEditable(), "聊天输入框"), "");
        } else {
            require(chatReady(target), "发送前会话标题未匹配");
            AccessibilityNodeInfo send = unique(named("发送"), "发送按钮");
            clicked[0] = true;
            click(send);
            pause();
            require(chatReady(target), "发送后会话标题未匹配");
            require(matching(n -> !n.isEditable() && text.contentEquals(value(n.getText()))).size() > before,
                "已点击发送，但未核对到完整文本");
        }
        return new JSONObject();
    }

    private JSONObject shareAsset(JSONObject request, String target, boolean[] clicked) throws Exception {
        require(!matching(named("最近聊天")).isEmpty(), "未确认微信分享会话选择页");
        String kind = request.getString("kind");
        boolean image = kind.equals("image");
        boolean file = kind.equals("file");
        String content = kind.equals("text") ? request.getString("text") : request.optString("filename");
        Predicate<AccessibilityNodeInfo> contentMatch = file
            ? n -> named(content).test(n) || truncatedFilename(value(n.getText()).toString(), content)
            : named(content);
        Rect recent = new Rect();
        unique(named("最近聊天"), "最近聊天分区").getBoundsInScreen(recent);
        List<AccessibilityNodeInfo> rows = shareRows(target, recent.bottom);
        if (rows.isEmpty() && request.optBoolean("group")) {
            List<AccessibilityNodeInfo> folded = shareRows("折叠的聊天", recent.bottom);
            if (folded.size() == 1) {
                click(folded.get(0));
                pause();
                rows = shareRows(target, 0);
                if (rows.isEmpty()) {
                    require(performGlobalAction(GLOBAL_ACTION_BACK), "无法退出折叠列表");
                    pause();
                }
            }
        }
        if (rows.isEmpty()) {
            List<AccessibilityNodeInfo> inputs = matching(AccessibilityNodeInfo::isEditable);
            if (inputs.isEmpty()) {
                click(unique(named("搜索"), "微信分享搜索入口"));
                pause();
            }
            AccessibilityNodeInfo query = unique(AccessibilityNodeInfo::isEditable, "微信分享搜索框");
            setText(query, target);
            Rect bounds = new Rect();
            query.getBoundsInScreen(bounds);
            rows = shareRows(target, bounds.bottom);
        }
        require(rows.size() == 1, "分享会话匹配数为 " + rows.size() + "，请确认手机备注唯一且可搜索");
        click(rows.get(0));
        pause();
        AccessibilityNodeInfo send;
        try {
            List<AccessibilityNodeInfo> buttons = matching(named("发送"));
            for (int attempt = 0; attempt < 5 && buttons.isEmpty(); attempt++) {
                pause();
                buttons = matching(named("发送"));
            }
            require(buttons.size() == 1, "分享确认发送按钮匹配数为 " + buttons.size()
                + "，微信可见节点 " + nodes().size());
            send = buttons.get(0);
            Rect button = new Rect();
            send.getBoundsInScreen(button);
            java.util.regex.Pattern groupLabel = java.util.regex.Pattern.compile(
                java.util.regex.Pattern.quote(target) + "\\s*[（(]\\d+人[）)]");
            boolean group = request.optBoolean("group");
            require(matching(n -> lowerSheet(n, button) && (named(target).test(n)
                || (group && (groupLabel.matcher(value(n.getText())).matches()
                    || groupLabel.matcher(value(n.getContentDescription())).matches())))).size() == 1,
                "分享确认页未核对到唯一收件人");
            if (request.getBoolean("dry_run")) {
                click(unique(named("取消"), "取消分享按钮"));
                return new JSONObject();
            }
        } catch (Exception error) {
            List<AccessibilityNodeInfo> cancel = matching(named("取消"));
            if (cancel.size() == 1) {
                try { click(cancel.get(0)); } catch (IOException ignored) {}
            }
            throw error;
        }
        clicked[0] = true;
        click(send);
        for (int attempt = 0; attempt < 8; attempt++) {
            pause();
            AccessibilityNodeInfo stay = sentConfirmation();
            if (stay != null) {
                try { click(stay); }
                catch (IOException error) { android.util.Log.w("WeChatSender", "发送已确认，未能关闭成功提示", error); }
                return new JSONObject();
            }
            if (matching(named("最近聊天")).isEmpty()
                && !matching(AccessibilityNodeInfo::isEditable).isEmpty()
                && !matching(image ? named("图片") : contentMatch).isEmpty()) {
                return new JSONObject();
            }
        }
        throw new IOException("已点击分享发送，但未核对到消息，请在会话中确认");
    }

    private AccessibilityNodeInfo sentConfirmation() {
        List<AccessibilityNodeInfo> stay = matching(named("留在微信"));
        return stay.size() == 1 && !matching(named("已发送")).isEmpty() ? stay.get(0) : null;
    }
    private static boolean truncatedFilename(String shown, String filename) {
        String[] parts = shown.split("\\.\\.\\.|…", -1);
        return parts.length == 2 && parts[0].length() >= 4 && parts[1].length() >= 4
            && filename.length() > parts[0].length() + parts[1].length()
            && filename.startsWith(parts[0]) && filename.endsWith(parts[1]);
    }
    private List<AccessibilityNodeInfo> shareRows(String target, int minTop) {
        List<AccessibilityNodeInfo> rows = new ArrayList<>();
        for (AccessibilityNodeInfo node : matching(n -> !n.isEditable() && target.contentEquals(value(n.getText())))) {
            Rect label = new Rect();
            node.getBoundsInScreen(label);
            if (label.top < minTop) continue;
            AccessibilityNodeInfo row = clickable(node);
            if (row == null) continue;
            Rect bounds = new Rect();
            row.getBoundsInScreen(bounds);
            boolean duplicate = false;
            for (AccessibilityNodeInfo prior : rows) {
                Rect other = new Rect();
                prior.getBoundsInScreen(other);
                if (Math.abs(bounds.centerY() - other.centerY()) < Math.min(bounds.height(), other.height()) / 3) {
                    duplicate = true;
                    break;
                }
            }
            if (!duplicate) rows.add(row);
        }
        return rows;
    }

    private boolean lowerSheet(AccessibilityNodeInfo node, Rect button) {
        Rect bounds = new Rect();
        node.getBoundsInScreen(bounds);
        return bounds.centerY() > getResources().getDisplayMetrics().heightPixels / 2
            && bounds.bottom < button.top;
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

    private boolean chatReady(String target) {
        if (inChat(target)) return true;
        return "文件传输助手".equals(target) && target.equals(preparedTarget)
            && matching(n -> n.isEditable() && "com.tencent.mm:id/bkk".equals(n.getViewIdResourceName())).size() == 1
            && matching(n -> "com.tencent.mm:id/fq".equals(n.getViewIdResourceName()) && named("聊天信息").test(n)).size() == 1;
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
        if (result.isEmpty()) {
            for (android.view.accessibility.AccessibilityWindowInfo window : getWindows()) {
                if (!window.isFocused() || window.getType() != android.view.accessibility.AccessibilityWindowInfo.TYPE_APPLICATION) continue;
                AccessibilityNodeInfo candidate = window.getRoot();
                if (candidate != null && WECHAT.contentEquals(value(candidate.getPackageName()))) {
                    collect(candidate, result);
                    break;
                }
            }
        }
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

    private static void clearDraft(AccessibilityNodeInfo input) throws Exception {
        if (!input.isShowingHintText() && value(input.getText()).length() > 0) {
            setText(input, "");
        }
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
        synchronized (Bridge.class) {
            if (activeBridge == this) activeBridge = null;
        }
        super.onDestroy();
    }
}
