using System;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Runtime.InteropServices;
using System.Text.Json;
using System.Windows.Forms;
using Microsoft.Web.WebView2.Core;
using Microsoft.Web.WebView2.WinForms;

namespace AppHost
{
    internal sealed class MainForm : Form
    {
        private static readonly Color Bg = Color.FromArgb(10, 11, 14);
        private readonly WebView2 web = new WebView2();
        private bool lightTheme;
        private bool allowClose;
        private bool waitingForFrontendFlush;
        private int closeRequestId;
        private readonly int port;
        private readonly System.Windows.Forms.Timer closeTimer = new();
        private readonly string statePath =
            Path.Combine(AppDomain.CurrentDomain.BaseDirectory, ".window.json");

        public MainForm(int port)
        {
            this.port = port;

            Text = "影音库";
            BackColor = Bg;
            ClientSize = new Size(1320, 820);
            MinimumSize = new Size(960, 600);
            StartPosition = FormStartPosition.CenterScreen;
            RestoreGeometry();

            var iconPath = Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "app.ico");
            if (File.Exists(iconPath))
            {
                try { Icon = new Icon(iconPath); } catch { }
            }

            web.Dock = DockStyle.Fill;
            web.DefaultBackgroundColor = Bg;
            Controls.Add(web);

            closeTimer.Interval = 10000;
            closeTimer.Tick += OnCloseFlushTimeout;
            Shown += OnShown;
            FormClosing += OnClosing;
        }

        private async void OnShown(object? sender, EventArgs e)
        {
            try
            {
                await web.EnsureCoreWebView2Async(null);
            }
            catch (Exception ex)
            {
                MessageBox.Show(
                    "WebView2 初始化失败：\n" + ex.Message +
                    "\n\n这台机器缺少 Microsoft Edge WebView2 运行时，装一下即可。",
                    "影音库", MessageBoxButtons.OK, MessageBoxIcon.Error);
                Close();
                return;
            }

            var core = web.CoreWebView2;
            core.Settings.AreDefaultContextMenusEnabled = false;
            core.Settings.AreDevToolsEnabled = false;
            core.Settings.IsStatusBarEnabled = false;
            core.Settings.IsSwipeNavigationEnabled = false;

            core.WebMessageReceived += OnWebMessage;
            core.NavigationStarting += OnNavigationStarting;
            core.NewWindowRequested += OnNewWindowRequested;

            web.Source = new Uri("http://127.0.0.1:" + port + "/?theme=" + (lightTheme ? "light" : "dark"));
        }

        private void OnWebMessage(object? sender, CoreWebView2WebMessageReceivedEventArgs e)
        {
            try
            {
                using var doc = JsonDocument.Parse(e.WebMessageAsJson);
                var root = doc.RootElement;
                if (root.TryGetProperty("type", out var type) && type.GetString() == "theme"
                    && root.TryGetProperty("value", out var value))
                {
                    ApplyTheme(value.GetString() == "light");
                    return;
                }
                if (root.TryGetProperty("type", out type)
                    && type.GetString() == "doubanLogin")
                {
                    BeginInvoke(new Action(() =>
                    {
                        using var login = new DoubanLoginForm(port);
                        login.ShowDialog(this);
                        try
                        {
                            web.CoreWebView2?.PostWebMessageAsJson(
                                JsonSerializer.Serialize(new
                                {
                                    type = "doubanSessionChanged",
                                    saved = login.Saved,
                                })
                            );
                        }
                        catch { }
                    }));
                    return;
                }
                if (root.TryGetProperty("type", out type) && type.GetString() == "closeReady"
                    && root.TryGetProperty("requestId", out var requestId)
                    && requestId.GetInt32() == closeRequestId)
                {
                    waitingForFrontendFlush = false;
                    closeTimer.Stop();
                    bool ok = root.TryGetProperty("ok", out var okValue)
                        && okValue.ValueKind == JsonValueKind.True;
                    int pending = root.TryGetProperty("pending", out var pendingValue)
                        ? pendingValue.GetInt32()
                        : 0;
                    int failures = root.TryGetProperty("failures", out var failuresValue)
                        ? failuresValue.GetInt32()
                        : 0;
                    if (ok)
                    {
                        allowClose = true;
                        Close();
                    }
                    else
                    {
                        string message = pending > 0
                            ? "还有 " + pending + " 条备注没有保存成功。"
                            : failures > 0
                                ? "还有 " + failures + " 项数据没有保存成功。"
                                : "前端数据未能安全保存。";
                        MessageBox.Show(
                            message + "\n\n窗口将保持打开。请处理保存失败的内容后再关闭。",
                            "影音库", MessageBoxButtons.OK, MessageBoxIcon.Warning);
                    }
                }
            }
            catch { }
        }

        // obsidian:// 这类自定义协议交给系统处理
        private void OnNavigationStarting(object? sender, CoreWebView2NavigationStartingEventArgs e)
        {
            if (IsExternal(e.Uri))
            {
                e.Cancel = true;
                OpenExternal(e.Uri);
            }
        }

        private void OnNewWindowRequested(object? sender, CoreWebView2NewWindowRequestedEventArgs e)
        {
            if (IsExternal(e.Uri))
            {
                e.Handled = true;
                OpenExternal(e.Uri);
            }
            else
            {
                e.Handled = true;
                web.Source = new Uri(e.Uri);
            }
        }

        private static bool IsExternal(string uri)
        {
            if (string.IsNullOrEmpty(uri)) return false;
            return uri.StartsWith("obsidian:", StringComparison.OrdinalIgnoreCase)
                || uri.StartsWith("mailto:", StringComparison.OrdinalIgnoreCase);
        }

        private static void OpenExternal(string uri)
        {
            try
            {
                Process.Start(new ProcessStartInfo(uri) { UseShellExecute = true });
            }
            catch { }
        }

        // ---- 深色标题栏（Win11 支持逐窗口设置）----
        private const int DWMWA_USE_IMMERSIVE_DARK_MODE = 20;
        private const int DWMWA_USE_IMMERSIVE_DARK_MODE_OLD = 19;
        private const int DWMWA_BORDER_COLOR = 34;
        private const int DWMWA_CAPTION_COLOR = 35;
        private const int DWMWA_TEXT_COLOR = 36;

        [DllImport("dwmapi.dll")]
        private static extern int DwmSetWindowAttribute(IntPtr hwnd, int attr, ref int value, int size);

        protected override void OnHandleCreated(EventArgs e)
        {
            base.OnHandleCreated(e);
            ApplyDarkTitleBar();
        }

        private void ApplyDarkTitleBar()
        {
            ApplyTheme(lightTheme);
        }

        private void ApplyTheme(bool light)
        {
            lightTheme = light;
            try
            {
                int dark = light ? 0 : 1;
                if (DwmSetWindowAttribute(Handle, DWMWA_USE_IMMERSIVE_DARK_MODE, ref dark, sizeof(int)) != 0)
                {
                    DwmSetWindowAttribute(Handle, DWMWA_USE_IMMERSIVE_DARK_MODE_OLD, ref dark, sizeof(int));
                }

                Color caption = light ? Color.FromArgb(0xF6, 0xF5, 0xF1) : Bg;
                Color border = light ? Color.FromArgb(0xE6, 0xE3, 0xDC) : Color.FromArgb(34, 36, 48);
                Color text = light ? Color.FromArgb(0x1D, 0x1F, 0x26) : Color.FromArgb(198, 205, 218);

                int c = ColorTranslator.ToWin32(caption);
                DwmSetWindowAttribute(Handle, DWMWA_CAPTION_COLOR, ref c, sizeof(int));
                int b = ColorTranslator.ToWin32(border);
                DwmSetWindowAttribute(Handle, DWMWA_BORDER_COLOR, ref b, sizeof(int));
                int tx = ColorTranslator.ToWin32(text);
                DwmSetWindowAttribute(Handle, DWMWA_TEXT_COLOR, ref tx, sizeof(int));

                BackColor = caption;
                if (web.DefaultBackgroundColor != caption) web.DefaultBackgroundColor = caption;
            }
            catch { }
        }

        private void RestoreGeometry()
        {
            try
            {
                if (!File.Exists(statePath)) return;
                using var doc = JsonDocument.Parse(File.ReadAllText(statePath));
                var r = doc.RootElement;
                int w = r.TryGetProperty("width", out var wv) ? wv.GetInt32() : 1360;
                int h = r.TryGetProperty("height", out var hv) ? hv.GetInt32() : 860;
                if (r.TryGetProperty("theme", out var tv) && tv.GetString() == "light") lightTheme = true;
                int x = r.TryGetProperty("x", out var xv) ? xv.GetInt32() : 0;
                int y = r.TryGetProperty("y", out var yv) ? yv.GetInt32() : 0;
                bool maximized = r.TryGetProperty("maximized", out var mv)
                    && mv.ValueKind == JsonValueKind.True;

                var targetScreen = Screen.FromPoint(new Point(x, y));
                var area = targetScreen.WorkingArea;

                // 2.5.6 and earlier saved maximized geometry without a state flag.
                // Treat near-fullscreen/negative legacy bounds as maximized instead
                // of reopening a clipped normal window.
                bool legacyMaximized = !r.TryGetProperty("maximized", out _)
                    && w >= area.Width - 24
                    && h >= area.Height - 24;
                if (maximized || legacyMaximized)
                {
                    WindowState = FormWindowState.Maximized;
                    return;
                }

                StartPosition = FormStartPosition.Manual;
                Bounds = ClampToWorkingArea(new Rectangle(x, y, w, h), area, MinimumSize);
            }
            catch { }
        }

        internal static Rectangle ClampToWorkingArea(
            Rectangle requested,
            Rectangle workingArea,
            Size minimumSize)
        {
            int maxWidth = Math.Max(minimumSize.Width, workingArea.Width - 48);
            int maxHeight = Math.Max(minimumSize.Height, workingArea.Height - 48);
            int width = Math.Clamp(requested.Width, minimumSize.Width, maxWidth);
            int height = Math.Clamp(requested.Height, minimumSize.Height, maxHeight);
            int x = Math.Clamp(
                requested.X,
                workingArea.Left,
                Math.Max(workingArea.Left, workingArea.Right - width));
            int y = Math.Clamp(
                requested.Y,
                workingArea.Top,
                Math.Max(workingArea.Top, workingArea.Bottom - height));
            return new Rectangle(x, y, width, height);
        }

        private void OnClosing(object? sender, FormClosingEventArgs e)
        {
            if (!allowClose)
            {
                e.Cancel = true;
                if (waitingForFrontendFlush) return;
                var core = web.CoreWebView2;
                if (core == null)
                {
                    allowClose = true;
                    Close();
                    return;
                }
                waitingForFrontendFlush = true;
                closeRequestId++;
                try
                {
                    var request = JsonSerializer.Serialize(new
                    {
                        type = "prepareClose",
                        requestId = closeRequestId,
                    });
                    core.PostWebMessageAsJson(request);
                    closeTimer.Start();
                }
                catch
                {
                    waitingForFrontendFlush = false;
                    closeTimer.Stop();
                    MessageBox.Show(
                        "无法检查备注保存状态，窗口将保持打开。",
                        "影音库", MessageBoxButtons.OK, MessageBoxIcon.Warning);
                }
                return;
            }

            Program.StopServer();

            try
            {
                bool maximized = WindowState == FormWindowState.Maximized;
                var bounds = WindowState == FormWindowState.Normal ? Bounds : RestoreBounds;
                var json = JsonSerializer.Serialize(new
                {
                    width = bounds.Width,
                    height = bounds.Height,
                    x = bounds.X,
                    y = bounds.Y,
                    maximized,
                    theme = lightTheme ? "light" : "dark",
                });
                File.WriteAllText(statePath, json);
            }
            catch { }
        }

        private void OnCloseFlushTimeout(object? sender, EventArgs e)
        {
            closeTimer.Stop();
            if (!waitingForFrontendFlush) return;
            waitingForFrontendFlush = false;
            MessageBox.Show(
                "等待备注保存超时，窗口将保持打开。\n\n请稍后重试关闭，并检查保存失败提示。",
                "影音库", MessageBoxButtons.OK, MessageBoxIcon.Warning);
        }
    }
}
