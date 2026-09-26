/**
 * Z4 MotionCam ビューア（Google Apps Script）
 *
 * カメラ（Z4）への接続は、閲覧端末ではなく Google のサーバーが代わりに行います（UrlFetchApp）。
 * 閲覧端末は Google とだけ通信するので、セキュリティソフトがカメラへの直接接続を止める環境でも使えます。
 *
 * - カメラの一覧（名前・アドレス・ポート）はシート「カメラ一覧」に保存します。
 * - パスワードはシートには保存せず、スクリプトプロパティ（画面に出ない保存場所）に保存します。
 */

var SHEET_NAME = 'カメラ一覧';
/** 録画は Range 指定で分割して取得する（UrlFetchApp の応答は 1 回 50MB まで）。 */
var CHUNK_BYTES = 8 * 1024 * 1024;
var NAME_RE = /^\d{8}_\d{6}(_\d+)?\.mp4$/;
var HOST_RE = /^[A-Za-z0-9.\-]{1,253}$/;

// ---- 画面 ----

/** Web アプリとして開いたとき。 */
function doGet() {
  checkAccess_();
  return HtmlService.createHtmlOutputFromFile('Index')
      .setTitle('Z4 MotionCam')
      .addMetaTag('viewport', 'width=device-width, initial-scale=1');
}

/** スプレッドシートを開いたとき、メニューに「ビューアを開く」を追加する。 */
function onOpen() {
  SpreadsheetApp.getUi().createMenu('Z4 MotionCam')
      .addItem('ビューアを開く', 'showViewer')
      .addToUi();
}

/** スプレッドシートの上にビューアを表示する。 */
function showViewer() {
  var html = HtmlService.createHtmlOutputFromFile('Index').setWidth(1000).setHeight(780);
  SpreadsheetApp.getUi().showModelessDialog(html, 'Z4 MotionCam');
}

/**
 * 利用者の制限。スクリプトプロパティ ALLOWED_EMAILS（カンマ区切り）を設定した場合だけ有効。
 * 「ウェブアプリにアクセスしているユーザーとして実行」で公開したときに使う（README 参照）。
 */
function checkAccess_() {
  var allowed = PropertiesService.getScriptProperties().getProperty('ALLOWED_EMAILS');
  if (!allowed) return;
  var me = String(Session.getActiveUser().getEmail() || '').toLowerCase();
  var list = allowed.toLowerCase().split(/[\s,]+/);
  if (!me || list.indexOf(me) < 0) throw new Error('このビューアを使う権限がありません');
}

// ---- カメラの登録 ----

function sheet_() {
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var sh = ss.getSheetByName(SHEET_NAME);
  if (!sh) {
    sh = ss.insertSheet(SHEET_NAME);
    sh.appendRow(['ID', '名前', 'アドレス', 'ポート', '登録日時']);
    sh.setFrozenRows(1);
  }
  return sh;
}

/** 登録済みカメラ（パスワードは返さない）。 */
function listCameras() {
  checkAccess_();
  var rows = sheet_().getDataRange().getValues();
  var out = [];
  for (var i = 1; i < rows.length; i++) {
    if (!rows[i][0]) continue;
    out.push({ id: String(rows[i][0]), name: String(rows[i][1]), host: String(rows[i][2]), port: Number(rows[i][3]) });
  }
  return out;
}

function findCamera_(id) {
  var rows = sheet_().getDataRange().getValues();
  for (var i = 1; i < rows.length; i++) {
    if (String(rows[i][0]) === String(id)) {
      var pw = PropertiesService.getScriptProperties().getProperty('pw_' + id);
      if (!pw) throw new Error('このカメラのパスワードが見つかりません。登録し直してください');
      return { id: String(id), row: i + 1, name: String(rows[i][1]), host: String(rows[i][2]), port: Number(rows[i][3]), password: pw };
    }
  }
  throw new Error('カメラが見つかりません（一覧から削除された可能性があります）');
}

/**
 * カメラを登録する。保存する前に接続とパスワードを確かめる。
 * @param {{name:string, host:string, port:(string|number), password:string}} form
 */
function addCamera(form) {
  checkAccess_();
  var name = String(form.name || '').trim();
  var host = String(form.host || '').trim().replace(/^https?:\/\//, '').replace(/\/.*$/, '');
  var port = parseInt(form.port, 10);
  var password = String(form.password || '');
  if (!name || name.length > 30) throw new Error('名前は1〜30文字で入力してください');
  if (!HOST_RE.test(host)) throw new Error('アドレスはIPアドレスかドメイン名で入力してください（例: 133.201.33.64）');
  if (!(port >= 1 && port <= 65535)) throw new Error('ポートは1〜65535の数字で入力してください');
  if (password.length < 8) throw new Error('パスワードは8文字以上です（Z4の「外出先視聴用パスワード」）');

  // 保存前に実際につないで確かめる。
  var status = JSON.parse(call_({ host: host, port: port, password: password }, '/api/status').getContentText());
  if (!status || !status.state) throw new Error('Z4 MotionCam の応答ではありません');

  var lock = LockService.getScriptLock();
  lock.waitLock(10000);
  try {
    var id = Utilities.getUuid().replace(/-/g, '').slice(0, 8);
    PropertiesService.getScriptProperties().setProperty('pw_' + id, password);
    sheet_().appendRow([id, name, host, port, new Date()]);
    return { id: id, name: name, host: host, port: port };
  } finally {
    lock.releaseLock();
  }
}

function removeCamera(id) {
  checkAccess_();
  var cam = findCamera_(id);
  sheet_().deleteRow(cam.row);
  PropertiesService.getScriptProperties().deleteProperty('pw_' + cam.id);
  return true;
}

// ---- カメラへの中継 ----

/** Z4 の HTTPS サーバー（外出先視聴用ポート）へ接続する。自己署名証明書なので証明書の検証は行わない。 */
function call_(cam, path, opt) {
  opt = opt || {};
  var headers = { Authorization: 'Basic ' + Utilities.base64Encode(Utilities.newBlob('z4:' + cam.password).getBytes()) };
  for (var k in (opt.headers || {})) headers[k] = opt.headers[k];
  var params = {
    method: opt.method || 'get',
    headers: headers,
    validateHttpsCertificates: false,
    muteHttpExceptions: true,
    followRedirects: false
  };
  if (opt.payload !== undefined) {
    params.payload = opt.payload;
    params.contentType = opt.contentType;
  }
  var resp;
  try {
    resp = UrlFetchApp.fetch('https://' + cam.host + ':' + cam.port + path, params);
  } catch (e) {
    throw new Error('カメラに接続できません（アドレス・ポート・ポートマッピング・Z4の「外出先からの視聴」を確認）: ' + e.message);
  }
  var code = resp.getResponseCode();
  if (code === 200 || code === 206) return resp;
  if (code === 401) throw new Error('パスワードが違います');
  if (code === 429) throw new Error('パスワードの誤りが続いたため、カメラが15分間接続を止めています');
  if (code === 403) throw new Error('カメラが接続を拒否しました（Z4で外出先視聴用パスワードが設定されているか確認）');
  if (code === 503) return resp; // カメラ準備中・監視オフなど。呼び出し側で扱う
  throw new Error('カメラからエラーが返りました（HTTP ' + code + '）');
}

/** 状態（録画中か、電池、温度、監視オン・オフなど）。 */
function getStatus(id) {
  checkAccess_();
  return JSON.parse(call_(findCamera_(id), '/api/status').getContentText());
}

/** ライブ映像の 1 コマ（data URL）。映像がないとき（監視オフなど）は null。 */
function getSnapshot(id) {
  checkAccess_();
  var resp = call_(findCamera_(id), '/snapshot.jpg');
  if (resp.getResponseCode() !== 200) return null;
  return 'data:image/jpeg;base64,' + Utilities.base64Encode(resp.getContent());
}

/** 録画一覧（新しい順。name, size, duration[ms]）。 */
function listRecordings(id) {
  checkAccess_();
  return JSON.parse(call_(findCamera_(id), '/api/recordings').getContentText());
}

/**
 * 録画ファイルの一部（start バイト目から最大 8MB）。
 * @return {{data:string, start:number, next:number, total:number}} data は base64
 */
function getRecordingChunk(id, name, start) {
  checkAccess_();
  if (!NAME_RE.test(String(name))) throw new Error('録画名が不正です');
  start = Math.max(0, parseInt(start, 10) || 0);
  var resp = call_(findCamera_(id), '/rec/' + encodeURIComponent(name),
      { headers: { Range: 'bytes=' + start + '-' + (start + CHUNK_BYTES - 1) } });
  var bytes = resp.getContent();
  var headers = resp.getHeaders();
  var range = headers['Content-Range'] || headers['content-range'] || '';
  var m = /\/(\d+)\s*$/.exec(range);
  var total = m ? Number(m[1]) : bytes.length;
  return { data: Utilities.base64Encode(bytes), start: start, next: start + bytes.length, total: total };
}

/** 録画を削除する（まとめて削除も可）。録画中のファイルはカメラ側で削除されない。 */
function deleteRecordings(id, names) {
  checkAccess_();
  names = [].concat(names || []);
  for (var i = 0; i < names.length; i++) {
    if (!NAME_RE.test(String(names[i]))) throw new Error('録画名が不正です: ' + names[i]);
  }
  if (!names.length) return { deleted: [], failed: [] };
  var resp = call_(findCamera_(id), '/api/delete', {
    method: 'post',
    payload: names.join('\n'),
    contentType: 'text/plain; charset=utf-8',
    headers: { 'X-Z4-Action': 'delete' }
  });
  return JSON.parse(resp.getContentText());
}

/** 監視のオン・オフ。 */
function setMonitoring(id, on) {
  checkAccess_();
  var resp = call_(findCamera_(id), '/api/monitoring', {
    method: 'post',
    payload: 'on=' + (on ? 'true' : 'false'),
    contentType: 'application/x-www-form-urlencoded',
    headers: { 'X-Z4-Action': 'monitoring' }
  });
  return JSON.parse(resp.getContentText());
}
