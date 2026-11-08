using System;
using System.Collections.Generic;
using System.Drawing;
using System.IO;
using System.Linq;
using System.Net.Http;
using System.Text;
using System.Text.Json;
using System.Threading.Tasks;
using System.Windows.Forms;
using Microsoft.Web.WebView2.Core;
using Microsoft.Web.WebView2.WinForms;

namespace AppHost
{
    internal sealed class DoubanLoginForm : Form
    {
        private readonly int port;
        private readonly WebView2 web = new();
        private readonly Label status = new();
        private readonly Button saveButton = new();
        private readonly Button cancelButton = new();
        private bool initialized;

        public bool Saved { get; private set; }

        public DoubanLoginForm(int port)
        {
            this.port = port;
            Text = "登录豆瓣 · 影音库";
            StartPosition = FormStartPosition.CenterParent;
            ClientSize = new Size(980, 700);
            MinimumSize = new Size(720, 520);
            BackColor = Color.FromArgb(10, 11, 14);
            ForeColor = Color.FromArgb(215, 220, 229);

            var footer = new Panel
            {
                Dock = DockStyle.Bottom,
                Height = 58,
                BackColor = Color.FromArgb(17, 19, 25),
                Padding = new Padding(12, 10, 12, 10),
            };
            status.AutoSize = false;
            status.Dock = DockStyle.Fill;
            status.TextAlign = ContentAlignment.MiddleLeft;
            status.ForeColor = Color.FromArgb(157, 165, 179);
            status.Text = "请在窗口中完成豆瓣登录，然后点击“保存登录状态”。";

            cancelButton.Text = "取消";
            cancelButton.Dock = DockStyle.Right;
            cancelButton.Width = 86;
            cancelButton.Click += (_, _) => Close();

            saveButton.Text = "保存登录状态";
            saveButton.Dock = DockStyle.Right;
            saveButton.Width = 126;
            saveButton.Click += async (_, _) => await SaveLoginAsync();

            footer.Controls.Add(status);
            footer.Controls.Add(saveButton);
            footer.Controls.Add(cancelButton);

            web.Dock = DockStyle.Fill;
            web.DefaultBackgroundColor = Color.White;
            Controls.Add(web);
            Controls.Add(footer);
            Shown += OnShown;
            FormClosed += (_, _) =>
            {
                web.Dispose();
            };

            var iconPath = Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "app.ico");
            if (File.Exists(iconPath))
            {
                try { Icon = new Icon(iconPath); } catch { }
            }
        }

        private async void OnShown(object? sender, EventArgs e)
        {
            if (initialized) return;
            initialized = true;
            try
            {
                var userData = Path.Combine(
                    Path.GetTempPath(),
                    "media-vault-douban-login"
                );
                var environment = await CoreWebView2Environment.CreateAsync(
                    null,
                    userData,
                    null
                );
                await web.EnsureCoreWebView2Async(environment);
                web.CoreWebView2.Settings.AreDefaultContextMenusEnabled = true;
                web.CoreWebView2.Settings.AreDevToolsEnabled = false;
                web.CoreWebView2.Settings.IsStatusBarEnabled = false;
                web.CoreWebView2.NewWindowRequested += OnNewWindowRequested;
                web.CoreWebView2.Navigate("https://accounts.douban.com/passport/login");
            }
            catch (Exception ex)
            {
                status.ForeColor = Color.FromArgb(226, 104, 95);
                status.Text = "登录窗口初始化失败：" + ex.Message;
                saveButton.Enabled = false;
            }
        }

        private void OnNewWindowRequested(
            object? sender,
            CoreWebView2NewWindowRequestedEventArgs e
        )
        {
            e.Handled = true;
            if (!string.IsNullOrWhiteSpace(e.Uri))
            {
                web.CoreWebView2.Navigate(e.Uri);
            }
        }

        private async Task SaveLoginAsync()
        {
            saveButton.Enabled = false;
            status.ForeColor = Color.FromArgb(157, 165, 179);
            status.Text = "正在读取豆瓣登录 Cookie…";
            try
            {
                var cookies = new Dictionary<string, string>(StringComparer.Ordinal);
                foreach (var uri in new[]
                {
                    "https://www.douban.com/",
                    "https://accounts.douban.com/",
                    "https://movie.douban.com/",
                })
                {
                    var values = await web.CoreWebView2.CookieManager.GetCookiesAsync(uri);
                    foreach (var cookie in values)
                    {
                        if (!string.IsNullOrWhiteSpace(cookie.Name))
                        {
                            cookies[cookie.Name] = cookie.Value ?? "";
                        }
                    }
                }
                if (!cookies.ContainsKey("dbcl2"))
                {
                    status.ForeColor = Color.FromArgb(226, 104, 95);
                    status.Text = "还没有检测到豆瓣登录状态，请先在窗口中完成登录。";
                    return;
                }
                var cookieHeader = string.Join(
                    "; ",
                    cookies.Select(pair => pair.Key + "=" + pair.Value)
                );
                var handler = new HttpClientHandler
                {
                    UseProxy = false,
                    UseCookies = false,
                };
                using var client = new HttpClient(handler)
                {
                    Timeout = TimeSpan.FromSeconds(15),
                };
                client.DefaultRequestHeaders.Add("X-MediaVault-Client", "media-vault");
                var body = JsonSerializer.Serialize(new { cookie = cookieHeader });
                using var content = new StringContent(
                    body,
                    Encoding.UTF8,
                    "application/json"
                );
                using var response = await client.PostAsync(
                    "http://127.0.0.1:" + port + "/api/douban/session",
                    content
                );
                var responseText = await response.Content.ReadAsStringAsync();
                if (!response.IsSuccessStatusCode)
                {
                    throw new InvalidOperationException(
                        ExtractError(responseText) ?? "登录态保存失败"
                    );
                }
                Saved = true;
                MessageBox.Show(
                    this,
                    "豆瓣登录状态已加密保存。现在可以搜索电影、剧集、图书和游戏。",
                    "影音库",
                    MessageBoxButtons.OK,
                    MessageBoxIcon.Information
                );
                DialogResult = DialogResult.OK;
                Close();
            }
            catch (Exception ex)
            {
                status.ForeColor = Color.FromArgb(226, 104, 95);
                status.Text = "保存失败：" + ex.Message;
            }
            finally
            {
                saveButton.Enabled = !Saved;
            }
        }

        private static string? ExtractError(string json)
        {
            try
            {
                using var doc = JsonDocument.Parse(json);
                return doc.RootElement.TryGetProperty("error", out var error)
                    ? error.GetString()
                    : null;
            }
            catch
            {
                return null;
            }
        }
    }
}
