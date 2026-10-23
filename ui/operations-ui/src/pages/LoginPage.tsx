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
    loginWithGoogleRedirect,
    handleAuthCallback,
    handleIdTokenCallback,
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

  // Handle incoming OAuth 2.0 / OIDC callback (query params for code/error, hash for id_token)
  useEffect(() => {
    const hash = location.hash.startsWith("#") ? location.hash.substring(1) : location.hash;
    const hashParams = new URLSearchParams(hash);
    const hashIdToken = hashParams.get("id_token");
    const combinedError =
      oauthError || hashParams.get("error_description") || hashParams.get("error");

    if (combinedError) {
      setCallbackError(`OIDC Provider Error: ${combinedError}`);
      if (typeof window !== "undefined" && window.history && window.history.replaceState) {
        window.history.replaceState(null, "", window.location.pathname);
      }
      return;
    }

    // 1. Google ID Token callback (via URL fragment #id_token=...)
    if (hashIdToken && !callbackHandledRef.current && !isAuthenticated) {
      callbackHandledRef.current = true;
      setIsProcessingCallback(true);
      handleIdTokenCallback(location.hash)
        .then(() => {
          navigate(from, { replace: true });
        })
        .catch((err: unknown) => {
          const msg =
            err instanceof Error ? err.message : "Authentication token verification failed";
          setCallbackError(msg);
          setIsProcessingCallback(false);
        });
      return;
    }

    // 2. Standard PKCE Authorization Code callback (via query parameters ?code=...&state=...)
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
  }, [
    code,
    state,
    oauthError,
    location.hash,
    handleAuthCallback,
    handleIdTokenCallback,
    isAuthenticated,
    navigate,
    from,
  ]);

  const displayError = callbackError || authError;

  return (
    <div className="min-h-screen bg-[var(--bg-app)] flex flex-col items-center justify-center p-4 relative selection:bg-indigo-100 selection:text-indigo-900">
      {/* Top Banner with Public System Status */}
      <div className="absolute top-5 right-6 flex items-center gap-3">
        <SystemStatusIndicator />
      </div>

      <div className="max-w-md w-full space-y-6 text-left">
        {/* Brand Identity */}
        <div className="text-center space-y-2">
          <div className="w-12 h-12 rounded-xl bg-indigo-600 flex items-center justify-center font-bold text-white text-lg mx-auto shadow-sm shadow-indigo-200">
            E7
          </div>
          <div>
            <h1 className="text-xl font-semibold text-[var(--text-primary)] tracking-tight">
              IR Infotech Operations Engine
            </h1>
            <p className="text-xs text-[var(--text-muted)] mt-1">
              Enterprise Workflow Runtime, Planning &amp; Constraint Scheduling
            </p>
          </div>
        </div>

        {/* Global Error Alert */}
        {displayError && (
          <div className="p-3.5 bg-red-50 border border-red-200 rounded-lg text-red-800 text-xs flex items-start gap-2.5 shadow-sm">
            <ShieldAlert className="w-4 h-4 text-red-600 shrink-0 mt-0.5" />
            <span className="leading-relaxed font-medium">{displayError}</span>
          </div>
        )}

        {/* Case 0: Exchanging Authorization Code Callback */}
        {isProcessingCallback || authLoading ? (
          <Card>
            <CardHeader>
              <div>
                <CardTitle className="flex items-center gap-2 text-base">
                  <Loader2 className="w-4 h-4 text-indigo-600 animate-spin" />
                  <span>Verifying OIDC Authentication</span>
                </CardTitle>
                <p className="text-xs text-[var(--text-muted)] mt-1">
                  Exchanging authorization code &amp; validating cryptographic JWT identity
                </p>
              </div>
              <Badge variant="primary" size="sm">
                Authenticating
              </Badge>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="p-6 bg-slate-50 border border-slate-200 rounded-lg text-center space-y-2.5">
                <Loader2 className="w-8 h-8 text-indigo-600 animate-spin mx-auto" />
                <p className="text-sm font-medium text-[var(--text-primary)]">Validating Session Identity</p>
                <p className="text-xs text-[var(--text-muted)]">
                  Contacting backend gateway at <code className="text-indigo-600 font-mono text-[11px] bg-white px-1 py-0.5 rounded border border-slate-200">/api/v1/auth/me</code>...
                </p>
              </div>
            </CardContent>
          </Card>
        ) : oidcConfig.isDemoLocalOidc ? (
          /* Case 1: DEMO Local Keycloak OIDC Configured */
          <Card className="shadow-sm border-[var(--border-subtle)]">
            <CardHeader className="mb-4">
              <div>
                <CardTitle className="flex items-center gap-2 text-base">
                  <KeyRound className="w-4 h-4 text-emerald-600" />
                  <span>Local Single Sign-On (Keycloak)</span>
                </CardTitle>
                <p className="text-xs text-[var(--text-muted)] mt-1">
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
                className="w-full flex items-center justify-center gap-2 font-medium shadow-sm shadow-indigo-100"
                onClick={() => {
                  loginWithPkceRedirect();
                }}
              >
                <LogIn className="w-4 h-4" />
                <span>Sign in with Keycloak (Demo SSO)</span>
              </Button>

              <div className="p-3.5 bg-slate-50 border border-slate-200 rounded-lg space-y-2 text-[var(--text-muted)] font-mono text-[11px]">
                <div className="flex justify-between border-b border-slate-200 pb-1.5">
                  <span className="text-[var(--text-muted)]">Identity Provider:</span>
                  <span className="text-[var(--text-primary)] font-medium">Keycloak (Port 8180)</span>
                </div>
                <div className="flex justify-between border-b border-slate-200 pb-1.5">
                  <span className="text-[var(--text-muted)]">Realm / Client:</span>
                  <span className="text-indigo-600 font-semibold">operations-engine-demo / operations-ui</span>
                </div>
                <div className="flex justify-between border-b border-slate-200 pb-1.5">
                  <span className="text-[var(--text-muted)]">Auth Flow:</span>
                  <span className="text-emerald-700 font-semibold">Authorization Code + PKCE (S256)</span>
                </div>
                <div className="flex justify-between pt-0.5">
                  <span className="text-[var(--text-muted)]">Demo Personas:</span>
                  <span className="text-[var(--text-secondary)] font-medium">planner, approver, scheduler, admin</span>
                </div>
              </div>

              <div className="p-3 bg-emerald-50/70 border border-emerald-200 rounded-lg space-y-1 text-emerald-900 text-[11px]">
                <div className="font-semibold flex items-center gap-1.5 text-emerald-800">
                  <UserCheck className="w-3.5 h-3.5" /> Real Authentication Enforcement
                </div>
                <p className="text-emerald-700 leading-relaxed">
                  Real OIDC tokens are cryptographically verified by the backend using Keycloak&apos;s JWKS endpoint.
                  Role-based permissions and tenant boundaries are enforced on all API routes.
                </p>
              </div>
            </CardContent>
          </Card>
        ) : oidcConfig.isConfigured ? (
          /* Case 2: Production Google Workspace OIDC Configured */
          <Card className="shadow-sm border-[var(--border-subtle)]">
            <CardHeader className="border-b border-[var(--border-subtle)] pb-4">
              <div>
                <CardTitle className="flex items-center gap-2 text-base">
                  <LogIn className="w-4 h-4 text-indigo-600" />
                  <span>Institutional Single Sign-On</span>
                </CardTitle>
                <p className="text-xs text-[var(--text-muted)] mt-1">
                  Authenticate using your corporate Google Workspace identity
                </p>
              </div>
              <Badge variant="primary" size="sm">
                OIDC Ready
              </Badge>
            </CardHeader>
            <CardContent className="space-y-4 pt-4">
              <Button
                variant="primary"
                size="lg"
                className="w-full flex items-center justify-center gap-2 font-medium"
                onClick={() => {
                  loginWithGoogleRedirect();
                }}
              >
                <ShieldCheck className="w-4 h-4" />
                <span>Sign in with Google Workspace</span>
              </Button>
            </CardContent>
          </Card>
        ) : (
          /* Case 3: OIDC Client Not Yet Provisioned by Company IT */
          <Card className="shadow-sm border-[var(--border-subtle)]">
            <CardHeader className="border-b border-[var(--border-subtle)] pb-4">
              <div>
                <CardTitle className="flex items-center gap-2 text-base">
                  <ShieldAlert className="w-4 h-4 text-amber-600" />
                  <span>Authentication Configuration Required</span>
                </CardTitle>
                <p className="text-xs text-[var(--text-muted)] mt-1">
                  Institutional Single Sign-On (SSO) is awaiting enterprise client registration
                </p>
              </div>
              <Badge variant="warning" size="sm">
                OIDC Pending
              </Badge>
            </CardHeader>
            <CardContent className="space-y-4 text-xs pt-4">
              <p className="text-[var(--text-secondary)] leading-relaxed">
                Browser access to the Operations Engine requires human identity verification via{" "}
                <span className="text-[var(--text-primary)] font-semibold">Google Workspace OpenID Connect (OIDC)</span>.
                The OAuth 2.0 Web Client ID corresponding to audience{" "}
                <code className="text-indigo-700 bg-slate-100 px-1.5 py-0.5 rounded font-mono text-[11px] border border-slate-200">
                  {oidcConfig.audience}
                </code>{" "}
                has not yet been provisioned by company IT for origin{" "}
                <code className="text-indigo-700 bg-slate-100 px-1.5 py-0.5 rounded font-mono text-[11px] border border-slate-200">
                  {oidcConfig.redirectUri}
                </code>.
              </p>

              <div className="p-3.5 bg-slate-50 border border-slate-200 rounded-lg space-y-2 text-[var(--text-muted)] font-mono text-[11px]">
                <div className="flex justify-between border-b border-slate-200 pb-1.5">
                  <span className="text-[var(--text-muted)]">Required Identity Provider:</span>
                  <span className="text-[var(--text-primary)] font-medium">Google Workspace (accounts.google.com)</span>
                </div>
                <div className="flex justify-between border-b border-slate-200 pb-1.5">
                  <span className="text-[var(--text-muted)]">Backend Token Audience:</span>
                  <span className="text-indigo-700 font-bold">{oidcConfig.audience}</span>
                </div>
                <div className="flex justify-between border-b border-slate-200 pb-1.5">
                  <span className="text-[var(--text-muted)]">Authorized Origin:</span>
                  <span className="text-[var(--text-primary)] font-medium">{oidcConfig.redirectUri}</span>
                </div>
                <div className="flex justify-between pt-0.5">
                  <span className="text-[var(--text-muted)]">Human Browser Auth State:</span>
                  <span className="text-amber-700 font-bold">BLOCKED_ON_COMPANY_OIDC_CLIENT_CONFIGURATION</span>
                </div>
              </div>

              <div className="p-3 bg-amber-50 border border-amber-200 rounded-lg space-y-1.5 text-amber-900 text-[11px]">
                <div className="font-semibold flex items-center gap-1.5 text-amber-800">
                  <ExternalLink className="w-3.5 h-3.5" /> Security Invariant
                </div>
                <p className="text-amber-800 leading-relaxed">
                  Machine-to-machine service credentials (<code>e7sk_...</code>) are strictly prohibited from
                  browser login screens. Automated backend tests utilize ephemeral service keys via direct HTTP calls,
                  while the user interface adheres to verified human SSO standards.
                </p>
              </div>
            </CardContent>
          </Card>
        )}

        {/* Institutional System Footer */}
        <div className="text-center text-xs text-[var(--text-muted)] space-y-1">
          <div className="flex items-center justify-center gap-2">
            <Server className="w-3.5 h-3.5 text-emerald-600" />
            <span className="text-[var(--text-secondary)] font-medium">Backend Gateway Operational (Port 8088)</span>
          </div>
          <div>All API requests bound to authenticated tenant context</div>
        </div>
      </div>
    </div>
  );
};
