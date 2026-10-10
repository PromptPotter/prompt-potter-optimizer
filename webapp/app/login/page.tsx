import { BRAND } from "@/lib/brand";
import { AuthCore } from "@/components/login/AuthCore";
import { BrandShowcase } from "@/components/login/BrandShowcase";

export default function LoginPage() {
  return (
    <div className="login-split">
      <div className="login-auth">
        <main className="login-container">
          <h1>{BRAND.shortName}</h1>
          <p className="login-sub">Sign in to continue</p>
          <AuthCore />
        </main>
      </div>
      {BRAND.marketing.url ? <BrandShowcase /> : null}
    </div>
  );
}
