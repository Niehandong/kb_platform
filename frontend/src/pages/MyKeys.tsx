import KeyManager from "../components/KeyManager";

export default function MyKeys() {
  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h1>我的密钥</h1>
          <p>
            用密钥可以让程序以你的身份调用召回接口。密钥能检索的知识库和你本人完全一样，所以不要交给别人；应用需要长期使用时，请让平台管理员创建服务账号。
          </p>
        </div>
      </div>
      <KeyManager basePath="/me/keys" />
    </div>
  );
}
