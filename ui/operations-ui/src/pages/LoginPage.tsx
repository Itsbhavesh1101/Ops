import React, { useEffect, useState, useRef } from "react";
import { useNavigate, useLocation } from "react-router-dom";
import { useAuth } from "../context/AuthContext";
import { Button } from "../components/ui/Button";
import { Card, CardHeader, CardTitle, CardContent } from "../components/ui/Card";
import { Badge } from "../components/ui/Badge";
import { SystemStatusIndicator } from "../components/layout/SystemStatusIndicator";
import {
  ShieldAlert,
  ShieldCheck,
  LogIn,
  Server,
  ExternalLink,
  Loader2,
  KeyRound,
  UserCheck,
} from "lucide-react";

export const LoginPage: React.FC = () => {
  const {
    error: authError,
    isAuthenticated,
    oidcConfig,
    loginWithPkceRedirect,
    handleAuthCallback,
    isLoading: authLoading,
  } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();

  const [callbackError, setCallbackError] = useState<string | null>(null);
  const [isProcessingCallback, setIsProcessingCallback] = useState(false);
  const callbackHandledRef = useRef(false);

  const searchParams = new URLSearchParams(location.search);
  const code = searchParams.get("code");
  const state = searchParams.get("state");
  const oauthError = searchParams.get("error_description") || searchParams.get("error");

  const from = (location.state as { from?: { pathname: string } })?.from?.pathname || "/dashboard";

  // Redirect if already authenticated
  useEffect(() => {
    if (isAuthenticated) {
      navigate(from, { replace: true });
    }
  }, [isAuthenticated, navigate, from]);

  // Handle incoming OAuth 2.0 / OIDC Authorization Code callback
  useEffect(() => {
    if (oauthError) {
      setCallbackError(`OIDC Provider Error: ${oauthError}`);
      return;
    }

    if (code && state && !callbackHandledRef.current && !isAuthenticated) {
      callbackHandledRef.current = true;
      setIsProcessingCallback(true);
      handleAuthCallback(code, state)
        .then(() => {
          navigate(from, { replace: true });
        })
        .catch((err: unknown) => {
          const msg =
            err instanceof Error ? err.message : "Authentication callback failed";
          setCallbackError(msg);
          setIsProcessingCallback(false);
        });
    }
  }, [code, state, oauthError, handleAuthCallback, isAuthenticated, navigate, from]);

  const displayError = callbackError || authError;

  return (
    <div className="min-h-screen bg-slate-950 flex flex-col items-center justify-center p-4">
      {/* Top Banner with Public System Status */}
      <div className="absolute top-4 right-4 flex items-center gap-3">
        <SystemStatusIndicator />
      </div>

      <div className="max-w-lg w-full space-y-6 text-left">
        {/* Brand Identity */}
        <div className="text-center space-y-2">
          <div className="w-12 h-12 rounded-xl bg-indigo-600 flex items-center justify-center font-black text-white text-xl mx-auto shadow-lg shadow-indigo-500/25">
            E7
          </div>
          <h1 className="text-2xl font-bold text-white tracking-tight">
            IR Infotech Operations Engine
          </h1>
          <p className="text-xs text-slate-400">
            Enterprise Workflow Runtime, Planning &amp; Constraint Scheduling
          </p>
        </div>

        {/* Global Error Alert */}
        {displayError && (
          <div className="p-3.5 bg-rose-950/60 border border-rose-800 rounded-xl text-rose-200 text-xs flex items-start gap-2.5">
            <ShieldAlert className="w-4 h-4 text-rose-400 shrink-0 mt-0.5" />
            <span className="leading-relaxed">{displayError}</span>
          </div>
        )}

        {/* Case 0: Exchanging Authorization Code Callback */}
        {isProcessingCallback || authLoading ? (
          <Card>
            <CardHeader>
              <div>
                <CardTitle className="flex items-center gap-2">
                  <Loader2 className="w-4 h-4 text-indigo-400 animate-spin" />
                  <span>Verifying OIDC Authentication</span>
                </CardTitle>
                <p className="text-xs text-slate-400 mt-1">
                  Exchanging authorization code &amp; validating cryptographic JWT identity
                </p>
              </div>
              <Badge variant="primary" size="sm">
                Authenticating
              </Badge>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="p-4 bg-slate-900/80 border border-slate-800 rounded-xl text-center space-y-2">
                <Loader2 className="w-8 h-8 text-indigo-500 animate-spin mx-auto" />
                <p className="text-sm font-medium text-white">Validating Session Identity</p>
                <p className="text-xs text-slate-400">
                  Contacting backend gateway at <code className="text-indigo-300 font-mono">/api/v1/auth/me</code>...
                </p>
              </div>
            </CardContent>
          </Card>
        ) : oidcConfig.isDemoLocalOidc ? (
          /* Case 1: DEMO Local Keycloak OIDC Configured */
          <Card>
            <CardHeader>
              <div>
                <CardTitle className="flex items-center gap-2">
                  <KeyRound className="w-4 h-4 text-emerald-400" />
                  <span>Local Single Sign-On (Keycloak)</span>
                </CardTitle>
                <p className="text-xs text-slate-400 mt-1">
                  Standards-compliant OIDC with Authorization Code + PKCE
                </p>
              </div>
              <Badge variant="success" size="sm">
                Local OIDC Ready
              </Badge>
            </CardHeader>
            <CardContent className="space-y-4">
              <Button
                variant="primary"
                size="lg"
                className="w-full flex items-center justify-center gap-2"
                onClick={() => {
                  loginWithPkceRedirect();
                }}
              >
                <LogIn className="w-4 h-4" />
                <span>Sign in with Keycloak (Demo SSO)</span>
              </Button>

              <div className="p-3.5 bg-slate-950/70 border border-slate-800 rounded-xl space-y-2 text-slate-400 font-mono text-[11px]">
                <div className="flex justify-between border-b border-slate-800/80 pb-1.5">
                  <span className="text-slate-400">Identity Provider:</span>
                  <span className="text-slate-200">Keycloak (Port 8180)</span>
                </div>
                <div className="flex justify-between border-b border-slate-800/80 pb-1.5">
                  <span className="text-slate-400">Realm / Client:</span>
                  <span className="text-indigo-300 font-bold">operations-engine-demo / operations-ui</span>
                </div>
                <div className="flex justify-between border-b border-slate-800/80 pb-1.5">
                  <span className="text-slate-400">Auth Flow:</span>
                  <span className="text-emerald-400 font-bold">Authorization Code + PKCE (S256)</span>
                </div>
                <div className="flex justify-between pt-0.5">
                  <span className="text-slate-400">Demo Personas:</span>
                  <span className="text-slate-200">planner, approver, scheduler, admin</span>
                </div>
              </div>

              <div className="p-3 bg-emerald-950/20 border border-emerald-900/40 rounded-xl space-y-1.5 text-emerald-300 text-[11px]">
                <div className="font-semibold flex items-center gap-1.5 text-emerald-200">
                  <UserCheck className="w-3.5 h-3.5" /> Real Authentication Enforcement
                </div>
                <p className="text-emerald-300/90 leading-relaxed">
                  Real OIDC tokens are cryptographically verified by the backend using Keycloak&apos;s JWKS endpoint.
                  Role-based permissions and tenant boundaries are enforced on all API routes.
                </p>
              </div>
            </CardContent>
          </Card>
        ) : oidcConfig.isConfigured ? (
          /* Case 2: Production Google Workspace OIDC Configured */
          <Card>
            <CardHeader>
              <div>
                <CardTitle className="flex items-center gap-2">
                  <LogIn className="w-4 h-4 text-indigo-400" />
                  <span>Institutional Single Sign-On</span>
                </CardTitle>
                <p className="text-xs text-slate-400 mt-1">
                  Authenticate using your corporate Google Workspace identity
                </p>
              </div>
              <Badge variant="primary" size="sm">
                OIDC Ready
              </Badge>
            </CardHeader>
            <CardContent className="space-y-4">
              <Button
                variant="primary"
                size="lg"
                className="w-full flex items-center justify-center gap-2"
                onClick={() => {
                  const params = new URLSearchParams({
                    client_id: oidcConfig.clientId,
                    redirect_uri: oidcConfig.redirectUri,
                    response_type: "id_token",
                    scope: "openid email profile",
                    nonce: Math.random().toString(36).substring(2),
                  });
                  window.location.href = `https://accounts.google.com/o/oauth2/v2/auth?${params.toString()}`;
                }}
              >
                <ShieldCheck className="w-4 h-4" />
                <span>Sign in with Google Workspace</span>
              </Button>
            </CardContent>
          </Card>
        ) : (
          /* Case 3: OIDC Client Not Yet Provisioned by Company IT */
          <Card>
            <CardHeader>
              <div>
                <CardTitle className="flex items-center gap-2">
                  <ShieldAlert className="w-4 h-4 text-amber-400" />
                  <span>Authentication Configuration Required</span>
                </CardTitle>
                <p className="text-xs text-slate-400 mt-1">
                  Institutional Single Sign-On (SSO) is awaiting enterprise client registration
                </p>
              </div>
              <Badge variant="warning" size="sm">
                OIDC Pending
              </Badge>
            </CardHeader>
            <CardContent className="space-y-4 text-xs">
              <p className="text-slate-300 leading-relaxed">
                Browser access to the Operations Engine requires human identity verification via{" "}
                <span className="text-white font-semibold">Google Workspace OpenID Connect (OIDC)</span>.
                The OAuth 2.0 Web Client ID corresponding to audience{" "}
                <code className="text-indigo-400 bg-slate-950 px-1.5 py-0.5 rounded font-mono text-[11px] border border-slate-800">
                  {oidcConfig.audience}
                </code>{" "}
                has not yet been provisioned by company IT for origin{" "}
                <code className="text-indigo-400 bg-slate-950 px-1.5 py-0.5 rounded font-mono text-[11px] border border-slate-800">
                  {oidcConfig.redirectUri}
                </code>.
              </p>

              <div className="p-3.5 bg-slate-950/70 border border-slate-800 rounded-xl space-y-2 text-slate-400 font-mono text-[11px]">
                <div className="flex justify-between border-b border-slate-800/80 pb-1.5">
                  <span className="text-slate-400">Required Identity Provider:</span>
                  <span className="text-slate-200">Google Workspace (accounts.google.com)</span>
                </div>
                <div className="flex justify-between border-b border-slate-800/80 pb-1.5">
                  <span className="text-slate-400">Backend Token Audience:</span>
                  <span className="text-indigo-300 font-bold">{oidcConfig.audience}</span>
                </div>
                <div className="flex justify-between border-b border-slate-800/80 pb-1.5">
                  <span className="text-slate-400">Authorized Origin:</span>
                  <span className="text-slate-200">{oidcConfig.redirectUri}</span>
                </div>
                <div className="flex justify-between pt-0.5">
                  <span className="text-slate-400">Human Browser Auth State:</span>
                  <span className="text-amber-400 font-bold">BLOCKED_ON_COMPANY_OIDC_CLIENT_CONFIGURATION</span>
                </div>
              </div>

              <div className="p-3 bg-amber-950/30 border border-amber-900/60 rounded-xl space-y-1.5 text-amber-300 text-[11px]">
                <div className="font-semibold flex items-center gap-1.5 text-amber-200">
                  <ExternalLink className="w-3.5 h-3.5" /> Security Invariant
                </div>
                <p className="text-amber-300/90 leading-relaxed">
                  Machine-to-machine service credentials (<code>e7sk_...</code>) are strictly prohibited from
                  browser login screens. Automated backend tests utilize ephemeral service keys via direct HTTP calls,
                  while the user interface adheres to verified human SSO standards.
                </p>
              </div>
            </CardContent>
          </Card>
        )}

        {/* Institutional System Footer */}
        <div className="text-center text-xs text-slate-400 space-y-1">
          <div className="flex items-center justify-center gap-2">
            <Server className="w-3.5 h-3.5 text-emerald-400" />
            <span className="text-slate-300 font-medium">Backend Gateway Operational (Port 8088)</span>
          </div>
          <div>All API requests bound to authenticated tenant context</div>
        </div>
      </div>
    </div>
  );
};
