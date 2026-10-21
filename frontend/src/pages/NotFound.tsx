import { Link } from "react-router-dom";
import { EmptyState } from "../components/ui";

export function NotFound() {
  return (
    <div className="panel">
      <EmptyState title="Page not found" action={<Link className="btn" to="/">Back to overview</Link>}>
        The page you requested does not exist.
      </EmptyState>
    </div>
  );
}
